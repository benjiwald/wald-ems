"""Loadpoint — Wallbox-Regelschleife (evcc-aligned).

Modi:
- OFF:    Laden gesperrt
- NOW:    Sofort laden mit maximalem Strom
- PV:     Nur mit PV-Überschuss laden (pausiert bei zu wenig)
- MIN_PV: Mindestladung (6A) + PV-Überschuss obendrauf

Regelung orientiert sich an evcc:
- Enable Delay: 60s (Überschuss muss 60s anstehen)
- Disable Delay: 180s (3 Min Wolken-Toleranz)
- Session Tracking: Status-basiert (A=getrennt beendet Session)
- Charger Grace Period: 60s nach Enable/Disable
- Phasen-Erkennung: > 1.0A Schwelle
- Kein EWMA — Enable/Disable Delays reichen als Filter
"""

import logging
import time
from api.charger import Charger
from api.meter import Meter
from api.interfaces import PhaseCurrents

log = logging.getLogger("ems.loadpoint")

# Konstanten (evcc-Defaults)
VOLTAGE = 230  # V (Nennspannung)
MIN_CURRENT = 6.0  # A (Minimum nach IEC 61851)
DEFAULT_MAX_CURRENT = 16.0  # A
CHARGER_SWITCH_DURATION = 60  # s — Grace Period nach Enable/Disable (wie evcc)
PHASE_ACTIVE_THRESHOLD = 1.0  # A — Phase gilt als aktiv ab 1A (wie evcc)


class ChargingSession:
    """Tracking einer einzelnen Ladesitzung."""

    def __init__(self, loadpoint_id: str, mode: str, phases: int):
        self.loadpoint_id = loadpoint_id
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.energy_wh: float = 0
        self.max_power_w: float = 0
        self.mode = mode
        self.phases = phases
        self.vehicle_soc_start: float | None = None
        self.vehicle_soc_end: float | None = None
        self._last_power_w: float = 0
        self._last_update: float = time.time()

    def update(self, power_w: float):
        """Aktualisiert Energie basierend auf aktueller Leistung."""
        now = time.time()
        dt_h = (now - self._last_update) / 3600  # Stunden
        self.energy_wh += self._last_power_w * dt_h
        self.max_power_w = max(self.max_power_w, power_w)
        self._last_power_w = power_w
        self._last_update = now

    def finish(self):
        self.update(0)
        self.finished_at = time.time()

    @property
    def duration_s(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    @property
    def energy_kwh(self) -> float:
        return self.energy_wh / 1000

    @property
    def avg_power_w(self) -> float:
        if self.duration_s <= 0:
            return 0
        return self.energy_wh / (self.duration_s / 3600)

    def to_dict(self) -> dict:
        from datetime import datetime, timezone
        return {
            "loadpoint_id": self.loadpoint_id,
            "loadpoint_name": self.loadpoint_id,
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": datetime.fromtimestamp(self.finished_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
            "duration_s": round(self.duration_s),
            "energy_kwh": round(self.energy_kwh, 2),
            "avg_power_w": round(self.avg_power_w),
            "max_power_w": round(self.max_power_w),
            "mode": self.mode,
            "phases": self.phases,
            "solar_kwh": 0,
            "vehicle": None,
            "cost_eur": 0,
            "active": self.finished_at is None,
        }


class Loadpoint:
    """Regelt eine einzelne Wallbox basierend auf verfügbarer Leistung."""

    def __init__(self, config: dict, charger: Charger, meter: Meter | None = None):
        self.id = config.get("id", "")
        self.name = config.get("name", "Ladepunkt")
        self.mode = config.get("mode", "off")
        self.min_current = float(config.get("min_current", MIN_CURRENT))
        self.max_current = float(config.get("max_current", DEFAULT_MAX_CURRENT))
        self.phases = int(config.get("phases", 3))
        self.priority = int(config.get("priority", 0))
        self.circuit_id = config.get("circuit_id")

        # evcc Settings
        self.target_soc = float(config.get("target_soc", 80))
        self.min_soc = float(config.get("min_soc", 20))
        self.battery_boost = bool(config.get("battery_boost", False))

        # Hysterese (evcc-Defaults)
        default_threshold = self.min_current * VOLTAGE * self.phases
        self.enable_threshold_w = float(config.get("enable_threshold_w", default_threshold))
        self.enable_delay_s = int(config.get("enable_delay_s", 60))      # evcc: 1 Min
        self.disable_threshold_w = float(config.get("disable_threshold_w", default_threshold))
        self.disable_delay_s = int(config.get("disable_delay_s", 180))   # evcc: 3 Min

        self.charger = charger
        self.meter = meter

        # Zustandsvariablen
        self._status = "A"
        self._prev_status = "A"
        self._charging_power_w = 0.0
        self._target_current_a = 0.0
        self._enabled = False

        # Hysterese Timer
        self._enable_timer: float | None = None
        self._disable_timer: float | None = None

        # Charger Grace Period (evcc: 60s nach Enable/Disable)
        self._charger_switch_time: float = 0

        # Write-on-change + periodisches Nachschreiben
        self._last_written_current: float = -1
        self._last_written_enabled: bool | None = None
        self._ever_enabled: bool = False
        self._last_write_time: float = 0

        # Session Tracking
        self._session: ChargingSession | None = None
        self._completed_sessions: list[dict] = []

        # Smart Cost
        self.cost_limit_ct: float = float(config.get("cost_limit_ct", 0))

        # Tariff Reference (wird von Site gesetzt)
        self.tariff = None

        # Vehicle Reference (wird von Site gesetzt)
        self.vehicle_soc: float | None = None

    def update(self, available_w: float) -> float:
        """Regelzyklus: Liest Status, berechnet Strom, schreibt an Charger.

        Returns:
            Tatsächlich genutzter Strom in Watt
        """
        # 1. Charger-Status lesen
        self._status = self.charger.status()

        # 2. Aktuelle Ladeleistung messen
        if self._status == "A":
            self._charging_power_w = 0
        elif isinstance(self.charger, Meter):
            self._charging_power_w = abs(self.charger.current_power())
        elif self.meter:
            self._charging_power_w = abs(self.meter.current_power())
        else:
            self._charging_power_w = 0

        # 3. Session aktualisieren (Status-basiert wie evcc)
        self._update_session()

        # Kein Fahrzeug verbunden → nichts zu tun
        if self._status == "A":
            self._target_current_a = 0
            self._enabled = False
            self._enable_timer = None
            self._disable_timer = None
            return 0

        # 4. Target SoC prüfen
        if self.vehicle_soc is not None and self.vehicle_soc > 0 and self.vehicle_soc >= self.target_soc:
            if self.mode in ("pv", "min_pv", "now"):
                log.info("LP %s: Target SoC %.0f%% erreicht (aktuell %.0f%%) — Laden gestoppt",
                         self.name, self.target_soc, self.vehicle_soc)
                self._set_charging(False, 0)
                return 0

        # 5. Min SoC prüfen — erzwingt Laden wenn unter Minimum
        force_charge = False
        if self.vehicle_soc is not None and self.vehicle_soc > 0 and self.vehicle_soc < self.min_soc:
            force_charge = True
            log.info("LP %s: Min SoC %.0f%% — erzwinge Laden (aktuell %.0f%%)",
                     self.name, self.min_soc, self.vehicle_soc)

        # 6. Zielstrom berechnen basierend auf Modus
        target_a = self._calculate_target(available_w, force_charge)

        # 7. Hysterese im PV-Modus
        if self.mode == "pv":
            target_a = self._apply_hysteresis(target_a, available_w)

        # 8. Min/Max Grenzen
        if target_a < self.min_current:
            if self.mode == "pv":
                target_a = 0  # PV: lieber aus als unter Minimum
            elif self.mode in ("min_pv", "now") or force_charge:
                target_a = self.min_current
            else:
                target_a = 0
        target_a = min(target_a, self.max_current)

        # 9. An Charger schreiben
        should_enable = target_a >= self.min_current
        if should_enable:
            self._ever_enabled = True
            self._set_charging(True, target_a)
        elif self._ever_enabled:
            self._set_charging(False, target_a)

        # 10. Aktive Phasenerkennung
        active_phases = self.phases
        if should_enable and isinstance(self.charger, PhaseCurrents):
            active_phases = self._detect_active_phases()

        used_w = target_a * VOLTAGE * active_phases if should_enable else 0
        log.info(
            "LP %s: mode=%s status=%s target=%.1fA power=%.0fW available=%.0fW phases=%d/%d soc=%s",
            self.name, self.mode, self._status, target_a,
            self._charging_power_w, available_w, active_phases, self.phases,
            f"{self.vehicle_soc:.0f}%" if self.vehicle_soc is not None else "—",
        )
        return used_w

    def _calculate_target(self, available_w: float, force_charge: bool) -> float:
        if self.mode == "off" and not force_charge:
            return 0

        if force_charge:
            return self.max_current

        if self.mode == "now":
            if self.tariff and self.cost_limit_ct > 0:
                if not self.tariff.is_cheap and self.tariff.current_price_ct > self.cost_limit_ct:
                    log.info("LP %s: Smart Cost — Preis %.1f ct > Limit %.1f ct -> pausiert",
                             self.name, self.tariff.current_price_ct, self.cost_limit_ct)
                    return 0
            return self.max_current

        available_a = available_w / (VOLTAGE * self.phases)

        if self.mode == "pv":
            return available_a

        if self.mode == "min_pv":
            return max(self.min_current, available_a)

        return 0

    def _apply_hysteresis(self, target_a: float, available_w: float) -> float:
        """Hysterese wie evcc: Enable/Disable Delays als zeitlicher Filter."""
        now = time.time()

        if not self._enabled:
            # Noch nicht aktiv → Enable-Threshold prüfen
            if available_w >= self.enable_threshold_w:
                if self._enable_timer is None:
                    self._enable_timer = now
                    log.debug("LP %s: Enable-Timer gestartet (%.0fW >= %.0fW)",
                              self.name, available_w, self.enable_threshold_w)
                elif now - self._enable_timer >= self.enable_delay_s:
                    log.info("LP %s: PV Enable — %.0fW für %ds verfügbar",
                             self.name, available_w, self.enable_delay_s)
                    self._enable_timer = None
                    return target_a  # Enable!
                return 0  # Noch warten
            else:
                self._enable_timer = None
                return 0  # Unter Threshold
        else:
            # Bereits aktiv → Disable prüfen wenn unter Minimum
            if target_a < self.min_current:
                if self._disable_timer is None:
                    self._disable_timer = now
                    log.debug("LP %s: Disable-Timer gestartet (%.1fA < %.1fA)",
                              self.name, target_a, self.min_current)
                elif now - self._disable_timer >= self.disable_delay_s:
                    log.info("LP %s: PV Disable — unter Minimum für %ds",
                             self.name, self.disable_delay_s)
                    self._disable_timer = None
                    return 0  # Disable!
                return self.min_current  # Noch halten mit Minimum
            else:
                self._disable_timer = None

        return target_a

    def _set_charging(self, enable: bool, target_a: float):
        """Setzt Charger-Status. Heartbeat nur fuer Strom-Setpoint.

        Enable/Disable: NUR bei Statusaenderung — Pause-Register (195)
        nicht wiederholt beschreiben, das startet NRG Kick Ladesession neu!

        Strom-Setpoint: bei Aenderung >= 0.5A ODER als Heartbeat alle 60s.
        Verhindert NRG Kick Modbus-Watchdog Timeout (~5min).
        """
        now = time.time()
        heartbeat = (now - self._last_write_time) >= 60

        # Enable/Disable: NUR bei Statusaenderung
        if enable != self._last_written_enabled:
            self.charger.enable(enable)
            self._last_written_enabled = enable
            self._enabled = enable
            self._charger_switch_time = now
            self._last_write_time = now

        # Strom-Setpoint: bei Aenderung oder Heartbeat (Register 194 ist safe)
        if enable and target_a >= self.min_current:
            if abs(target_a - self._last_written_current) >= 0.5 or heartbeat:
                self.charger.max_current(target_a)
                self._last_written_current = target_a
                self._last_write_time = now

        self._target_current_a = target_a
        self._enabled = enable

    def _detect_active_phases(self) -> int:
        """Erkennt aktive Phasen (evcc: > 1.0A Schwelle)."""
        # Grace Period nach Enable/Disable — Messwerte noch nicht stabil
        if time.time() - self._charger_switch_time < CHARGER_SWITCH_DURATION:
            return self.phases
        try:
            l1, l2, l3 = self.charger.currents()
            active = sum(1 for i in (l1, l2, l3) if i > PHASE_ACTIVE_THRESHOLD)
            if active > 0 and active != self.phases:
                log.warning("LP %s: Phasen-Abweichung! Config=%dP Gemessen=%dP (L1=%.1fA L2=%.1fA L3=%.1fA)",
                            self.name, self.phases, active, l1, l2, l3)
            return active if active > 0 else self.phases
        except Exception:
            return self.phases

    def _update_session(self):
        """Session Tracking — Status-basiert wie evcc.

        Session startet wenn Fahrzeug lädt (Status B/C mit Leistung).
        Session endet NUR wenn Fahrzeug abgesteckt wird (Status A).
        Kurze Leistungseinbrüche (Modbus-Glitches) beenden NICHT die Session.
        """
        if self._status in ("B", "C") and self._charging_power_w > 50:
            if self._session is None:
                self._session = ChargingSession(self.id, self.mode, self.phases)
                if self.vehicle_soc is not None:
                    self._session.vehicle_soc_start = self.vehicle_soc
                log.info("LP %s: Ladesession gestartet", self.name)
            else:
                self._session.update(self._charging_power_w)

        # Session beenden NUR bei Disconnect (Status A)
        if self._status == "A" and self._session is not None:
            if self.vehicle_soc is not None:
                self._session.vehicle_soc_end = self.vehicle_soc
            self._session.finish()
            if self._session.energy_kwh >= 0.01:
                self._completed_sessions.append(self._session.to_dict())
                log.info("LP %s: Ladesession beendet — %.2f kWh in %.0f Min",
                         self.name, self._session.energy_kwh,
                         self._session.duration_s / 60)
            else:
                log.debug("LP %s: Leere Session verworfen (%.4f kWh)", self.name, self._session.energy_kwh)
            self._session = None

    def set_mode(self, mode: str):
        if mode not in ("off", "now", "pv", "min_pv"):
            log.warning("Unbekannter Modus: %s", mode)
            return
        log.info("LP %s: Modus → %s", self.name, mode)
        self.mode = mode
        self._last_written_enabled = None
        self._last_written_current = -1
        # Timer zurücksetzen bei Moduswechsel
        self._enable_timer = None
        self._disable_timer = None
        if mode in ("off", "pv"):
            # OFF: sofort pausieren
            # PV: sofort pausieren → Enable-Logik entscheidet ob gestartet wird
            self.charger.enable(False)
            self._enabled = False
            self._last_written_enabled = False
            if mode == "off":
                log.info("LP %s: Wallbox sofort pausiert", self.name)
            else:
                log.info("LP %s: Wallbox pausiert — warte auf PV-Überschuss (%.0fW für %ds)",
                         self.name, self.enable_threshold_w, self.enable_delay_s)

    def set_target_soc(self, soc: float):
        self.target_soc = max(0, min(100, soc))
        log.info("LP %s: Target SoC → %.0f%%", self.name, self.target_soc)

    def set_min_soc(self, soc: float):
        self.min_soc = max(0, min(100, soc))
        log.info("LP %s: Min SoC → %.0f%%", self.name, self.min_soc)

    def set_max_current(self, current: float):
        self.max_current = max(self.min_current, min(32, current))
        log.info("LP %s: Max Strom → %.0fA", self.name, self.max_current)

    def pop_completed_sessions(self) -> list[dict]:
        sessions = self._completed_sessions.copy()
        self._completed_sessions.clear()
        return sessions

    def state(self) -> dict:
        result = {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "status": self._status,
            "charging_power_w": round(self._charging_power_w),
            "target_current_a": round(self._target_current_a, 1),
            "phases": self.phases,
            "enabled": self._enabled,
            "target_soc": self.target_soc,
            "min_soc": self.min_soc,
            "max_current": self.max_current,
            "cost_limit_ct": self.cost_limit_ct,
            "battery_boost": self.battery_boost,
            "vehicle_soc": self.vehicle_soc,
            "battery_kwh": getattr(self, '_vehicle_battery_kwh', None),
        }

        if self._session:
            result["session"] = self._session.to_dict()

        return result

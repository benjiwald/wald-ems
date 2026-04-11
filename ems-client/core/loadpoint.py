"""Loadpoint — Wallbox-Regelschleife (evcc-Feature-Parität).

Modi:
- OFF:    Laden gesperrt
- NOW:    Sofort laden mit maximalem Strom
- PV:     Nur mit PV-Überschuss laden (pausiert bei zu wenig)
- MIN_PV: Mindestladung (6A) + PV-Überschuss obendrauf

Features (wie evcc):
- Hysterese (enable/disable Threshold + Delay)
- Target SoC (stoppt bei Erreichen)
- Min SoC (erzwingt Laden unter Minimum)
- Phase Switching (1P↔3P basierend auf Leistung)
- Session Tracking (Energie, Dauer, Kosten)
"""

import logging
import time
from api.charger import Charger
from api.meter import Meter
from api.interfaces import PhaseCurrents

log = logging.getLogger("ems.loadpoint")

# Konstanten
VOLTAGE = 230  # V (Nennspannung)
MIN_CURRENT = 6.0  # A (Minimum nach IEC 61851)
DEFAULT_MAX_CURRENT = 16.0  # A
PHASE_SWITCH_THRESHOLD_1P = 1380  # W (6A × 230V × 1P) — unter dem: 1P reicht
PHASE_SWITCH_THRESHOLD_3P = 4140  # W (6A × 230V × 3P) — über dem: 3P nötig


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
            "loadpoint_name": self.loadpoint_id,  # write_session erwartet diesen Key
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": datetime.fromtimestamp(self.finished_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
            "duration_s": round(self.duration_s),
            "energy_kwh": round(self.energy_kwh, 2),
            "avg_power_w": round(self.avg_power_w),
            "max_power_w": round(self.max_power_w),
            "mode": self.mode,
            "phases": self.phases,
            "solar_kwh": 0,  # TODO: Solar-Anteil berechnen
            "vehicle": None,
            "cost_eur": 0,  # TODO: Kosten berechnen
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
        # Hysterese (evcc-Defaults):
        # Enable: 1380W (6A×230V×1P) für 60s bevor Laden startet
        # Disable: 5 Min warten bevor Laden gestoppt wird (Wolken-Toleranz)
        self.enable_threshold_w = float(config.get("enable_threshold_w", 1380))
        self.enable_delay_s = int(config.get("enable_delay_s", 60))
        self.disable_threshold_w = float(config.get("disable_threshold_w", 0))
        self.disable_delay_s = int(config.get("disable_delay_s", 300))

        self.charger = charger
        self.meter = meter

        # Zustandsvariablen
        self._status = "A"
        self._charging_power_w = 0.0
        self._target_current_a = 0.0
        self._enabled = False

        # Hysterese Timer
        self._enable_timer: float | None = None
        self._disable_timer: float | None = None

        # Write-on-change: letzter geschriebener Wert
        # Start mit None: erster Schreibvorgang wird immer ausgefuehrt,
        # ABER _set_charging wird erst aufgerufen wenn should_enable=True
        self._last_written_current: float = -1
        self._last_written_enabled: bool | None = None
        self._ever_enabled: bool = False  # True sobald einmal enabled
        self._last_write_time: float = 0  # Periodisches Nachschreiben

        # Session Tracking
        self._session: ChargingSession | None = None
        self._completed_sessions: list[dict] = []

        # Smart Cost
        self.cost_limit_ct: float = float(config.get("cost_limit_ct", 0))

        # Tariff Reference (wird von Site gesetzt)
        self.tariff = None  # AWATTarTariff instance

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
        # Charger-eigenes Meter bevorzugen (z.B. NRG Kick hat eingebauten Zaehler),
        # separates Meter nur wenn Charger kein Meter implementiert.
        if self._status == "A":
            self._charging_power_w = 0
        elif isinstance(self.charger, Meter):
            self._charging_power_w = abs(self.charger.current_power())
        elif self.meter:
            self._charging_power_w = abs(self.meter.current_power())
        else:
            self._charging_power_w = 0

        # 3. Session aktualisieren
        self._update_session()

        # Kein Fahrzeug verbunden → nichts zu tun
        if self._status == "A":
            self._target_current_a = 0
            self._enabled = False
            self._enable_timer = None
            self._disable_timer = None
            return 0

        # 4. Target/Min SoC — immer aus Loadpoint-Config (eine Quelle der Wahrheit)
        # Vehicle-Tabelle speichert nur den SoC-Wert, nicht das Ziel

        # Target SoC prüfen — Laden stoppen wenn erreicht (SoC 0 = unbekannt → ignorieren)
        if self.vehicle_soc is not None and self.vehicle_soc > 0 and self.vehicle_soc >= self.target_soc:
            if self.mode in ("pv", "min_pv", "now"):
                log.info("LP %s: Target SoC %.0f%% erreicht (aktuell %.0f%%) — Laden gestoppt",
                         self.name, self.target_soc, self.vehicle_soc)
                self._set_charging(False, 0)
                return 0

        # 5. Min SoC prüfen — erzwingt Laden wenn unter Minimum
        # SoC 0 = unbekannt (API nicht verfügbar) → NICHT force-chargen
        force_charge = False
        if self.vehicle_soc is not None and self.vehicle_soc > 0 and self.vehicle_soc < self.min_soc:
            force_charge = True
            log.info("LP %s: Min SoC %.0f%% — erzwinge Laden (aktuell %.0f%%)",
                     self.name, self.min_soc, self.vehicle_soc)

        # 6. Zielstrom berechnen basierend auf Modus
        target_a = self._calculate_target(available_w, force_charge)

        # 7. Hysterese nur im PV-Modus (Sofort und Min+PV starten sofort)
        if self.mode == "pv":
            target_a = self._apply_hysteresis(target_a, available_w)

        # 8. Min/Max Grenzen
        if target_a < self.min_current:
            if self.mode == "pv":
                target_a = 0  # PV: lieber aus als unter Minimum
            elif self.mode in ("min_pv", "now") or force_charge:
                target_a = self.min_current  # Min+PV/Sofort: mindestens 6A
            else:
                target_a = 0
        target_a = min(target_a, self.max_current)

        # 9. An Charger schreiben — aber NIE disable senden wenn noch nie enabled
        # (verhindert Unterbrechen einer laufenden Ladesession beim Start)
        should_enable = target_a >= self.min_current
        if should_enable:
            self._ever_enabled = True
            self._set_charging(True, target_a)
        elif self._ever_enabled:
            # Nur disablen wenn vorher schon mal enabled wurde
            self._set_charging(False, target_a)

        # Aktive Phasenerkennung: tatsächliche Phasen aus Charger-Strömen
        active_phases = self.phases
        if should_enable and isinstance(self.charger, PhaseCurrents):
            active_phases = self._detect_active_phases()

        # Berechnete Leistung (mit tatsächlichen Phasen für korrekte Bilanz)
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
            # Smart Cost: wenn Tarif konfiguriert und Preis über Limit -> pausieren
            if self.tariff and self.cost_limit_ct > 0:
                if not self.tariff.is_cheap and self.tariff.current_price_ct > self.cost_limit_ct:
                    log.info("LP %s: Smart Cost — Preis %.1f ct > Limit %.1f ct -> pausiert",
                             self.name, self.tariff.current_price_ct, self.cost_limit_ct)
                    return 0
            return self.max_current

        available_a = available_w / (VOLTAGE * self.phases)

        if self.mode == "pv":
            # Stufenweise Anpassung: max ±1A pro Zyklus (wie evcc)
            target = available_a
            if self._target_current_a > 0:
                diff = target - self._target_current_a
                if abs(diff) > 1.0:
                    target = self._target_current_a + (1.0 if diff > 0 else -1.0)
            return target

        if self.mode == "min_pv":
            # Min+PV: Mindestens min_current, darüber stufenweise ±1A
            target = max(self.min_current, available_a)
            if self._target_current_a >= self.min_current:
                diff = target - self._target_current_a
                if abs(diff) > 1.0:
                    target = self._target_current_a + (1.0 if diff > 0 else -1.0)
            return target

        return 0

    def _apply_hysteresis(self, target_a: float, available_w: float) -> float:
        """Hysterese: Verhindert zu häufiges Ein-/Ausschalten."""
        now = time.time()

        if not self._enabled:
            # Noch nicht aktiv → prüfe Enable-Threshold
            if self.enable_threshold_w > 0:
                if available_w >= self.enable_threshold_w:
                    if self._enable_timer is None:
                        self._enable_timer = now
                    elif now - self._enable_timer >= self.enable_delay_s:
                        self._enable_timer = None
                        return target_a  # Enable!
                    return 0  # Noch warten
                else:
                    self._enable_timer = None
                    return 0  # Unter Threshold
        else:
            # Bereits aktiv → prüfe Disable-Threshold
            if self.disable_threshold_w > 0 and target_a < self.min_current:
                if available_w <= -self.disable_threshold_w:
                    if self._disable_timer is None:
                        self._disable_timer = now
                    elif now - self._disable_timer >= self.disable_delay_s:
                        self._disable_timer = None
                        return 0  # Disable!
                    return self.min_current  # Noch halten
                else:
                    self._disable_timer = None

        return target_a

    def _set_charging(self, enable: bool, target_a: float):
        """Setzt Charger-Status und trackt Session. Schreibt nur bei Wertänderung.

        Periodisches Nachschreiben alle 60s verhindert, dass der Charger
        eigenmächtig den Setpoint/Phasen ändert (z.B. NRG Kick Reset).
        """
        now = time.time()
        force_rewrite = (now - self._last_write_time) > 60

        if enable != self._last_written_enabled or force_rewrite:
            self.charger.enable(enable)
            self._last_written_enabled = enable
            self._enabled = enable

        if enable and target_a >= self.min_current:
            # Schreiben bei Wertänderung ODER periodisch alle 60s
            if abs(target_a - self._last_written_current) >= 0.5 or force_rewrite:
                self.charger.max_current(target_a)
                self._last_written_current = target_a
                self._last_write_time = now

        self._target_current_a = target_a
        self._enabled = enable

    def _detect_active_phases(self) -> int:
        """Erkennt aktive Phasen aus den Charger-Phasenströmen."""
        try:
            l1, l2, l3 = self.charger.currents()
            active = sum(1 for i in (l1, l2, l3) if i > 0.5)
            if active > 0 and active != self.phases:
                log.warning("LP %s: Phasen-Abweichung! Config=%dP Gemessen=%dP (L1=%.1fA L2=%.1fA L3=%.1fA)",
                            self.name, self.phases, active, l1, l2, l3)
            return active if active > 0 else self.phases
        except Exception:
            return self.phases

    def _update_session(self):
        """Session Tracking: Start/Stop/Update."""
        # NRG Kick meldet manchmal Status B obwohl geladen wird → Power als Indikator
        is_charging = (self._status in ("B", "C")) and self._charging_power_w > 50

        if is_charging and self._session is None:
            # Neue Session starten
            self._session = ChargingSession(self.id, self.mode, self.phases)
            if self.vehicle_soc is not None:
                self._session.vehicle_soc_start = self.vehicle_soc
            log.info("LP %s: Ladesession gestartet", self.name)

        elif is_charging and self._session is not None:
            # Session aktualisieren
            self._session.update(self._charging_power_w)

        elif not is_charging and self._session is not None:
            # Session beenden
            if self.vehicle_soc is not None:
                self._session.vehicle_soc_end = self.vehicle_soc
            self._session.finish()
            # Nur Sessions mit Energie speichern (keine Ghost-Sessions)
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
        # Write-on-Change States zurücksetzen → nächster Zyklus schreibt definitiv
        self._last_written_enabled = None
        self._last_written_current = -1
        # Bei Off sofort Wallbox pausieren (nicht auf nächsten Zyklus warten)
        if mode == "off":
            self.charger.enable(False)
            self._enabled = False
            self._last_written_enabled = False
            log.info("LP %s: Wallbox sofort pausiert", self.name)

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
        """Gibt abgeschlossene Sessions zurück und leert die Liste."""
        sessions = self._completed_sessions.copy()
        self._completed_sessions.clear()
        return sessions

    def state(self) -> dict:
        """Gibt aktuellen Zustand für site_state zurück."""
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

        # Aktive Session
        if self._session:
            result["session"] = self._session.to_dict()

        return result

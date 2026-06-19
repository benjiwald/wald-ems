"""Loadpoint — Wallbox-Regelschleife (evcc-aligned).

Modi:
- OFF:    Laden gesperrt
- NOW:    Sofort laden mit maximalem Strom
- PV:     Nur mit PV-Überschuss laden (pausiert bei zu wenig)
- MIN_PV: Mindestladung (6A) + PV-Überschuss obendrauf

Regelung orientiert sich an evcc:
- Enable Delay: 20s (Überschuss muss 20s anstehen)
- Disable Delay: 300s (5 Min Wolken-Toleranz)
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
    """Tracking einer einzelnen Ladesitzung inkl. Solar/Grid-Aufteilung."""

    def __init__(self, loadpoint_id: str, mode: str, phases: int):
        self.loadpoint_id = loadpoint_id
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.energy_wh: float = 0
        self.solar_wh: float = 0   # aus PV (+ Hausbatterie)
        self.grid_wh: float = 0    # aus Netz
        self.max_power_w: float = 0
        self.mode = mode
        self.phases = phases
        self.vehicle_soc_start: float | None = None
        self.vehicle_soc_end: float | None = None
        self._last_power_w: float = 0
        self._last_solar_share: float = 1.0  # Default: 100% Solar
        self._last_update: float = time.time()

    def update(self, power_w: float, solar_share: float = 1.0):
        """Aktualisiert Energie basierend auf aktueller Leistung.

        solar_share: Anteil aus PV/Batterie (0.0 = alles Netz, 1.0 = alles Solar)
        """
        now = time.time()
        dt_h = (now - self._last_update) / 3600
        energy_increment = self._last_power_w * dt_h
        self.energy_wh += energy_increment
        share = max(0.0, min(1.0, self._last_solar_share))
        self.solar_wh += energy_increment * share
        self.grid_wh += energy_increment * (1.0 - share)
        self.max_power_w = max(self.max_power_w, power_w)
        self._last_power_w = power_w
        self._last_solar_share = solar_share
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
            "started_at_ts": self.started_at,
            "finished_at": datetime.fromtimestamp(self.finished_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
            "duration_s": round(self.duration_s),
            "energy_kwh": round(self.energy_kwh, 2),
            "solar_kwh": round(self.solar_wh / 1000, 2),
            "grid_kwh": round(self.grid_wh / 1000, 2),
            "avg_power_w": round(self.avg_power_w),
            "max_power_w": round(self.max_power_w),
            "mode": self.mode,
            "phases": self.phases,
            "vehicle_soc_start": self.vehicle_soc_start,
            "vehicle_soc_end": self.vehicle_soc_end,
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
        # Zombie-Wake-Up: CP-Signal-Toggle wenn Status B + 0W.
        # Standardmaessig AN — hilft bei den meisten EVs aus dem Schlaf.
        # ACHTUNG: Renault Zoe verliert dadurch die Session — bei Zoe abschalten.
        # Wird in main.build_site fuer Renault automatisch auf False gesetzt.
        self.zombie_wakeup_enabled = bool(config.get("zombie_wakeup_enabled", True))
        self.phases = int(config.get("phases", 3))
        self.priority = int(config.get("priority", 0))
        self.circuit_id = config.get("circuit_id")

        # evcc Settings
        self.target_soc = float(config.get("target_soc", 80))
        self.min_soc = float(config.get("min_soc", 20))
        self.battery_boost = bool(config.get("battery_boost", False))
        # Hard-Cap auf maximale Session-Energie als Safety-Net wenn die
        # SoC-Estimation kaputt ist (Renault Cloud-Lag o.ae.). Default 95%
        # der konfigurierten Batterie-Kapazitaet — bei 40 kWh-Akku also
        # 38 kWh Ladegrenze. Kann via 'session_limit_factor' ueberschrieben werden.
        self.session_limit_factor = float(config.get("session_limit_factor", 0.95))

        # Hysterese (evcc-Defaults)
        # Asymmetrische Hysterese — responsiv + Wolken-tolerant:
        # Enable: 4000W fuer 20s (reagiert schnell auf Sonne, evcc-aehnlich)
        # Disable: 0W fuer 300s (5 Min, toleriert Wolken, laedt durch mit min_current)
        self.enable_threshold_w = float(config.get("enable_threshold_w", 4000))
        self.enable_delay_s = int(config.get("enable_delay_s", 20))
        self.disable_threshold_w = float(config.get("disable_threshold_w", 0))
        self.disable_delay_s = int(config.get("disable_delay_s", 300))

        self.charger = charger
        self.meter = meter

        # Zustandsvariablen
        self._status = "A"
        self._prev_status = "A"
        self._charging_power_w = 0.0
        self._target_current_a = 0.0
        self._enabled = False
        self._last_solar_share: float = 1.0

        # Hysterese Timer
        self._enable_timer: float | None = None
        self._disable_timer: float | None = None

        # Charger Grace Period (evcc: 60s nach Enable/Disable)
        self._charger_switch_time: float = 0

        # Write-on-change
        self._last_written_current: float = -1
        self._last_written_enabled: bool | None = None
        self._ever_enabled: bool = False
        self._last_write_time: float = 0

        # Zombie-Wake-Up (evcc-Style): wenn enabled+connected aber kein Strom
        self._zombie_since: float | None = None
        self._last_wake_up: float = 0

        # Session Tracking
        self._session: ChargingSession | None = None
        self._completed_sessions: list[dict] = []

        # Smart Cost
        self.cost_limit_ct: float = float(config.get("cost_limit_ct", 0))

        # Tariff Reference (wird von Site gesetzt)
        self.tariff = None

        # Vehicle SoC Tracking (Session-basiert, evcc-style)
        # ---------------------------------------------------------------
        # Problem mit "Reset-on-API-Update": Wenn die Cloud-API immer
        # *hinter* der Realitaet ist (Renault MyR ist beruechtigt traege),
        # resetten wir den Estimation-Counter bei jedem frischen Wert auf 0
        # und fangen wieder bei Cloud-SoC an zu addieren — wir holen den Lag
        # also NIE auf, weil Cloud-SoC <= Realitaet bleibt.
        #
        # Loesung: Beim Plug-in (A→B) snapshoten wir die SoC als Baseline.
        # Die Wallbox-Energie wird ueber die GESAMTE Session aufaddiert
        # (monotonisch, ohne Reset bei API-Updates). Estimated berechnet:
        #
        #   estimated = max(api_soc, session_start_soc + delivered/battery*100)
        #
        # → Wenn die Cloud hinterher ist, gewinnt der Wallbox-Term.
        # → Wenn die Cloud mal voraus ist, gewinnt der Cloud-Wert.
        # → Estimated nimmt nie ab solange die Session laeuft.
        self._vehicle_soc_api: float | None = None
        self._vehicle_soc_api_ts: float = 0.0
        self._vehicle_battery_kwh: float = 0.0
        self._session_start_soc: float | None = None
        self._session_delivered_wh: float = 0.0
        self._last_estimation_ts: float = 0.0
        self._estimation_initialized: bool = False
        # DB-Persistenz: alle 30s in state-Tabelle (ueberlebt Service-Restart)
        self._last_persist_ts: float = 0.0
        # Plug-out-Hysterese: Status A muss seit > 60s konstant kommen,
        # sonst ignorieren (Modbus-Glitches zerstoeren nicht die Session).
        self._status_a_first_seen: float | None = None
        self._prev_status_effective: str = "A"

        # DB-Handle (optional, vom main.py gesetzt) — fuer Events + Session-Persistenz
        self._db = None

    # ── Vehicle SoC (Session-basierte Estimation) ────────────────────────
    def set_vehicle_soc_api(self, soc: float | None) -> None:
        """Setzt einen frischen SoC-Wert von der Cloud-API.

        Der Estimation-Counter wird NICHT resettet — die Wallbox-Energie
        laeuft monoton ueber die ganze Session. Wir merken uns nur den neuen
        API-Wert als untere Schranke fuer die Estimation.

        WICHTIG (Lazy-Init): wenn die Session schon laeuft (_session_start_soc
        ist None weil API beim Plug-in noch nichts geliefert hatte), holen wir
        das jetzt nach. Wir rekonstruieren den Start aus aktueller API minus
        der schon gelaufenen Wallbox-Energie.
        """
        # SANITY-FILTER — implausible Werte ignorieren.
        # Hintergrund: die Renault Kamereon API liefert gelegentlich 0% wenn
        # das Auto schlaeft oder bei Auth-Aussetzern.
        if soc is None:
            return
        try:
            soc_f = float(soc)
        except (TypeError, ValueError):
            return
        if not (1.0 <= soc_f <= 100.0):
            log.warning("LP %s: API lieferte implausible vehicle_soc=%.1f%% — ignoriert",
                        self.name, soc_f)
            return
        # Plausible Drops > 30% vom letzten Wert sind verdaechtig (API-Aussetzer)
        prev = self._vehicle_soc_api
        if prev is not None and (prev - soc_f) > 30:
            api_age = time.time() - self._vehicle_soc_api_ts
            if api_age < 1800:
                log.warning("LP %s: API SoC-Drop %.0f%% → %.0f%% verdaechtig (Alter %.0fs) — ignoriert",
                            self.name, prev, soc_f, api_age)
                return

        self._vehicle_soc_api = soc_f
        self._vehicle_soc_api_ts = time.time()
        # Lazy-Init: Session laeuft aber Start fehlt → ableiten
        if (self._session_start_soc is None
                and self._prev_status in ("B", "C")
                and self._vehicle_battery_kwh > 0):
            delta_pct = (self._session_delivered_wh / 1000.0) \
                        / self._vehicle_battery_kwh * 100.0
            self._session_start_soc = max(0.0, soc_f - delta_pct)
            log.info("LP %s: Session-Start lazy-init = %.1f%% (API %.0f%% − %.1f kWh schon geladen)",
                     self.name, self._session_start_soc, soc_f,
                     self._session_delivered_wh / 1000.0)
            if self._db is not None:
                try:
                    self._db.publish_log("info",
                        f"LP {self.name}: Session-Start lazy-init = {self._session_start_soc:.1f}% "
                        f"(API {soc_f:.0f}% nachgereicht, {self._session_delivered_wh/1000.0:.1f} kWh bereits geladen)")
                except Exception:
                    pass

    @property
    def vehicle_soc(self) -> float | None:
        """Estimated SoC — robust gegen Cloud-Lag.

        Reihenfolge:
        1. Wenn session_start + battery_kwh bekannt:
             max(api, session_start + delivered/battery*100)
        2. Wenn nur api da (Session-Start fehlt aber Energie geflossen):
             api + delivered/battery*100  (konservativer Fallback)
        3. Sonst api.
        """
        api = self._vehicle_soc_api
        if api is None:
            return None
        if self._vehicle_battery_kwh and self._vehicle_battery_kwh > 0:
            delta_pct = (self._session_delivered_wh / 1000.0) \
                        / self._vehicle_battery_kwh * 100.0
            if self._session_start_soc is not None:
                estimated_from_session = self._session_start_soc + delta_pct
                return max(0.0, min(100.0, max(api, estimated_from_session)))
            elif delta_pct > 0.5:
                return max(0.0, min(100.0, api + delta_pct))
        return max(0.0, min(100.0, api))

    @vehicle_soc.setter
    def vehicle_soc(self, value: float | None) -> None:
        """Backwards-kompatibel: direkt-Setzen verhaelt sich wie API-Setter."""
        self.set_vehicle_soc_api(value)

    # ── Session-Persistenz (ueberlebt Service-Restart) ───────────────────
    def _session_state_key(self) -> str:
        return f"loadpoint_{self.id}_session"

    def _persist_session_state(self) -> None:
        if self._db is None:
            return
        try:
            self._db.set_state(self._session_state_key(), {
                "start_soc": self._session_start_soc,
                "delivered_wh": self._session_delivered_wh,
                "ts": time.time(),
            })
        except Exception as e:
            log.debug("LP %s: _persist_session_state Fehler: %s", self.name, e)

    def _restore_session_state(self) -> bool:
        """Versucht Session-State aus DB zu laden. Returns True wenn erfolgreich."""
        if self._db is None:
            return False
        try:
            data = self._db.get_state(self._session_state_key())
            if not isinstance(data, dict):
                return False
            ts = data.get("ts", 0)
            if time.time() - ts > 7200:
                return False
            start_soc = data.get("start_soc")
            delivered_wh = float(data.get("delivered_wh", 0))
            if start_soc is not None and start_soc < 1.0:
                log.warning("LP %s: DB-Session-State hat invaliden start_soc=%.1f%% — verworfen",
                            self.name, start_soc)
                return False
            self._session_start_soc = start_soc
            self._session_delivered_wh = delivered_wh
            return True
        except Exception as e:
            log.debug("LP %s: _restore_session_state Fehler: %s", self.name, e)
            return False

    def _clear_session_state(self) -> None:
        if self._db is None:
            return
        try:
            self._db.set_state(self._session_state_key(), None)
        except Exception:
            pass

    def force_reset_session_estimation(self) -> dict:
        """Reset des SoC-Estimation-States. Behaelt Cloud-API-Wert."""
        before = self.diagnostic_soc_state()
        self._session_start_soc = None
        self._session_delivered_wh = 0.0
        self._estimation_initialized = False
        self._clear_session_state()
        log.info("LP %s: SoC-Estimation force-reset (war: %s)", self.name, before)
        return before

    def diagnostic_soc_state(self) -> dict:
        """Aktuelle SoC-Estimation Variablen fuer Debugging."""
        if self._vehicle_battery_kwh > 0:
            if self.vehicle_soc is None:
                soc_diff = max(0, self.target_soc - 20.0)
                cap = self._vehicle_battery_kwh * soc_diff / 100.0 / 0.88
                cap_mode = "no_soc_conservative"
            else:
                cap = self._vehicle_battery_kwh * self.session_limit_factor
                cap_mode = "normal"
        else:
            cap = None
            cap_mode = "no_battery_kwh"
        return {
            "api_soc": self._vehicle_soc_api,
            "api_age_s": (time.time() - self._vehicle_soc_api_ts) if self._vehicle_soc_api_ts else None,
            "session_start_soc": self._session_start_soc,
            "session_delivered_wh": round(self._session_delivered_wh, 1),
            "session_delivered_kwh": round(self._session_delivered_wh / 1000.0, 2),
            "battery_kwh": self._vehicle_battery_kwh,
            "estimated_soc": self.vehicle_soc,
            "target_soc": self.target_soc,
            "session_limit_kwh": round(cap, 2) if cap is not None else None,
            "session_limit_mode": cap_mode,
            "status": self._status,
            "prev_status": self._prev_status,
            "estimation_initialized": self._estimation_initialized,
            "enabled": self._last_written_enabled,
            "charging_power_w": self._charging_power_w,
        }

    def update(self, available_w: float, grid_import_w: float = 0,
               pv_surplus_w: float | None = None) -> float:
        """Regelzyklus: Liest Status, berechnet Strom, schreibt an Charger.

        Args:
            available_w: Gesamt verfuegbare Leistung (inkl. Batterie-Beitrag).
                         Wird im PV-Modus mit Hysterese verwendet.
            grid_import_w: Netzbezug (positiv=Import, negativ=Export)
            pv_surplus_w: Echter PV-Surplus = PV minus Hausgrundlast.
                          Wird im min_pv-Modus benutzt, damit der LP NICHT
                          die Batterie aggressiv leerzieht. Default: available_w.

        Returns:
            Tatsächlich genutzter Strom in Watt
        """
        if pv_surplus_w is None:
            pv_surplus_w = available_w
        # 1. Charger-Status lesen
        self._status = self.charger.status()

        # Zombie-Schutz nach Restart: Wenn der Charger beim (Re)Start bereits
        # geladen hat, _ever_enabled setzen — sonst greift der Disable-Pfad nie.
        if self._status == "C" and not self._ever_enabled:
            self._ever_enabled = True
            log.info("LP %s: Charger beim Start bereits aktiv — _ever_enabled=True gesetzt", self.name)

        # 2. Aktuelle Ladeleistung messen
        if self._status == "A":
            self._charging_power_w = 0
        elif isinstance(self.charger, Meter):
            self._charging_power_w = abs(self.charger.current_power())
        elif self.meter:
            self._charging_power_w = abs(self.meter.current_power())
        else:
            self._charging_power_w = 0

        # SoC-Estimation auf Session-Basis (evcc-style):
        # Beim Plug-in (A→B) snapshoten wir die SoC als Baseline. Wallbox-
        # Energie wird monoton ueber die ganze Session aufaddiert.
        now_ts = time.time()
        if self._last_estimation_ts == 0:
            self._last_estimation_ts = now_ts

        # SERVICE-RESTART-DETECTION: wenn der allererste poll bereits Status
        # B/C zeigt, ist das KEIN Plug-in — mitten in laufender Session neugestartet.
        if not self._estimation_initialized:
            self._estimation_initialized = True
            if self._status in ("B", "C"):
                restored = self._restore_session_state()
                if restored:
                    log.info("LP %s: Service-Restart waehrend laufender Session erkannt "
                             "(Status %s) — Session-State aus DB wiederhergestellt: "
                             "start_soc=%s, delivered=%.2f kWh",
                             self.name, self._status,
                             f"{self._session_start_soc:.0f}%" if self._session_start_soc is not None else "—",
                             self._session_delivered_wh / 1000.0)
                else:
                    log.warning("LP %s: Service-Restart waehrend laufender Session (Status %s) "
                                "— kein State in DB, lazy-init beim naechsten API-Update",
                                self.name, self._status)
                self._prev_status = self._status
                self._prev_status_effective = self._status

        # Plug-out-Hysterese: Status A muss seit > 60s konstant gemeldet werden,
        # sonst ignorieren (Modbus-Glitch zerstoert nicht die Session).
        PLUG_OUT_HYST_S = 60.0
        if self._status == "A":
            if self._status_a_first_seen is None:
                self._status_a_first_seen = now_ts
        else:
            if self._status_a_first_seen is not None:
                glitch_dur = now_ts - self._status_a_first_seen
                if glitch_dur < PLUG_OUT_HYST_S:
                    log.info("LP %s: Status-A-Glitch ignoriert (%.1fs) — Session bleibt",
                             self.name, glitch_dur)
            self._status_a_first_seen = None

        confirmed_a = (self._status_a_first_seen is not None and
                       (now_ts - self._status_a_first_seen) >= PLUG_OUT_HYST_S)
        effective_status = "A" if confirmed_a else (self._status if self._status != "A" else self._prev_status_effective)

        # A → B/C: Plug-in detektiert. Snapshot SoC als Session-Start.
        if self._prev_status_effective == "A" and effective_status in ("B", "C"):
            self._session_start_soc = self._vehicle_soc_api
            self._session_delivered_wh = 0.0
            log.info("LP %s: Plug-in detektiert (Status %s→%s) — Session-Start SoC = %s",
                     self.name, self._prev_status_effective, effective_status,
                     f"{self._session_start_soc:.0f}%" if self._session_start_soc is not None else "—")
            if self._db is not None:
                try:
                    self._db.publish_log("info",
                        f"LP {self.name}: Plug-in — Session-Start SoC = "
                        f"{'%.0f%%' % self._session_start_soc if self._session_start_soc is not None else 'unbekannt'}")
                except Exception:
                    pass

        # → A: Plug-out detektiert (nur confirmed). Session beenden + DB-State loeschen.
        if self._prev_status_effective != "A" and effective_status == "A":
            log.info("LP %s: Plug-out detektiert (confirmed nach %.0fs) — Session-Energy %.2f kWh",
                     self.name, PLUG_OUT_HYST_S, self._session_delivered_wh / 1000.0)
            self._session_start_soc = None
            self._session_delivered_wh = 0.0
            self._clear_session_state()

        # Wallbox-Energie zur Session aufaddieren — monoton, nie reset waehrend Session
        dt = now_ts - self._last_estimation_ts
        if 0 < dt < 600 and self._charging_power_w > 50 and self._status in ("B", "C"):
            self._session_delivered_wh += self._charging_power_w * (dt / 3600.0)
        self._last_estimation_ts = now_ts
        self._prev_status = self._status
        self._prev_status_effective = effective_status

        # Session-State alle 30s in DB persistieren (ueberlebt Restart)
        if self._status in ("B", "C") and (now_ts - self._last_persist_ts) > 30:
            self._persist_session_state()
            self._last_persist_ts = now_ts

        # Solar-Anteil berechnen
        if self._charging_power_w > 50 and grid_import_w > 0:
            grid_share = min(1.0, grid_import_w / self._charging_power_w)
            self._last_solar_share = max(0.0, 1.0 - grid_share)
        else:
            self._last_solar_share = 1.0

        # 3. Session aktualisieren (Status-basiert wie evcc)
        self._update_session()

        # 3a. Zombie-Wake-Up (evcc-Style):
        # Wenn enabled+verbunden (B) seit zu langer Zeit und kein Strom fliesst,
        # Fahrzeug ist wahrscheinlich eingeschlafen. CP-Signal togglen.
        #
        # ACHTUNG: Renault Zoe verliert dadurch die Session — zombie_wakeup_enabled
        # wird in main.py fuer Renault automatisch auf False gesetzt.
        #
        # Timeout ist Modus-abhaengig:
        #   now:    60s  — Sofort soll sofort laden, kurze Reaktion
        #   pv/min_pv: 300s — PV wartet auf Sonne, laengere Toleranz
        now = now_ts
        zombie_timeout = 60 if self.mode == "now" else 300
        if (self.zombie_wakeup_enabled
                and self._last_written_enabled and self._status == "B"
                and self._charging_power_w < 50 and self._ever_enabled):
            if self._zombie_since is None:
                self._zombie_since = now
            elif (now - self._zombie_since > zombie_timeout
                  and now - self._last_wake_up > 600):  # max alle 10 min
                log.warning("LP %s: Zombie erkannt (Status B ohne Strom seit %.0fs) — Wake-Up-Toggle",
                            self.name, now - self._zombie_since)
                try:
                    self.charger.enable(False)
                    time.sleep(1.0)
                    self.charger.enable(True)
                    self._last_wake_up = now
                    self._zombie_since = None
                except Exception as e:
                    log.error("LP %s: Wake-Up fehlgeschlagen: %s", self.name, e)
        else:
            self._zombie_since = None

        # Kein Fahrzeug verbunden → nichts zu tun
        if self._status == "A":
            self._target_current_a = 0
            self._enabled = False
            self._enable_timer = None
            self._disable_timer = None
            return 0

        # 4. STOP-Bedingungen — zwei unabhaengige Mechanismen wie evcc:
        #    (a) SoC-Stop: estimated >= target_soc
        #    (b) kWh-Safety-Cap: delivered > battery_kwh * session_limit_factor
        #        (Backup wenn SoC-Estimation kaputt ist)
        est_soc = self.vehicle_soc
        api_soc = self._vehicle_soc_api
        start_soc = self._session_start_soc
        delivered_kwh = self._session_delivered_wh / 1000.0

        soc_stop = (est_soc is not None and est_soc > 0
                    and est_soc >= self.target_soc
                    and self.mode in ("pv", "min_pv", "now"))

        if self._vehicle_battery_kwh > 0:
            if est_soc is None:
                # KEINE SoC-Daten: konservativ Annahme Start bei 20% SoC
                start_assumed = 20.0
                soc_diff = max(0, self.target_soc - start_assumed)
                kwh_cap = self._vehicle_battery_kwh * soc_diff / 100.0 / 0.88
            else:
                kwh_cap = self._vehicle_battery_kwh * self.session_limit_factor
        else:
            kwh_cap = None

        energy_stop = (kwh_cap is not None and delivered_kwh >= kwh_cap
                       and self.mode in ("pv", "min_pv", "now"))

        if soc_stop or energy_stop:
            first_stop = self._last_written_enabled is True
            reason = "Target SoC erreicht" if soc_stop else "Session-Energy-Cap erreicht (Safety-Net)"
            msg = (f"LP {self.name}: {reason} — gestoppt. "
                   f"estimated={est_soc if est_soc is not None else -1:.1f}%, "
                   f"API={api_soc if api_soc is not None else -1:.0f}%, "
                   f"target={self.target_soc:.0f}%, "
                   f"Session-Start={start_soc if start_soc is not None else -1:.0f}%, "
                   f"delivered={delivered_kwh:.1f} kWh"
                   f"{f' (Cap {kwh_cap:.1f} kWh)' if energy_stop else ''}")
            log.info(msg)
            if self._db is not None and (first_stop
                                          or (time.time() - getattr(self, '_last_stop_log_ts', 0)) > 3600):
                try:
                    self._db.publish_log("warning" if energy_stop else "info", msg)
                    self._last_stop_log_ts = time.time()
                except Exception as e:
                    log.debug("publish_log fehlgeschlagen: %s", e)
            self._set_charging(False, 0)
            return 0

        # 5. Min SoC prüfen — erzwingt Laden wenn unter Minimum
        force_charge = False
        if self.vehicle_soc is not None and self.vehicle_soc > 0 and self.vehicle_soc < self.min_soc:
            force_charge = True
            log.info("LP %s: Min SoC %.0f%% — erzwinge Laden (aktuell %.0f%%)",
                     self.name, self.min_soc, self.vehicle_soc)

        # 6. Zielstrom berechnen basierend auf Modus
        target_a = self._calculate_target(available_w, force_charge, pv_surplus_w)

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

        # 10. Aktive Phasen: immer aus Config (Zoe: NIE umschalten, immer 3P)
        active_phases = self.phases

        used_w = target_a * VOLTAGE * active_phases if should_enable else 0
        log.info(
            "LP %s: mode=%s status=%s target=%.1fA power=%.0fW available=%.0fW phases=%d/%d soc=%s",
            self.name, self.mode, self._status, target_a,
            self._charging_power_w, available_w, active_phases, self.phases,
            f"{self.vehicle_soc:.0f}%" if self.vehicle_soc is not None else "—",
        )
        return used_w

    def _calculate_target(self, available_w: float, force_charge: bool,
                          pv_surplus_w: float | None = None) -> float:
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

        if self.mode == "pv":
            return available_w / (VOLTAGE * self.phases)

        if self.mode == "min_pv":
            # Min+PV: min_current als Untergrenze, max_current wenn echter PV-Surplus.
            # WICHTIG: pv_surplus_w (= PV minus Hausgrundlast) statt available_w,
            # damit nicht aggressiv die Batterie entladen wird wenn PV niedrig ist.
            surplus_w = pv_surplus_w if pv_surplus_w is not None else available_w
            return max(self.min_current, surplus_w / (VOLTAGE * self.phases))

        return 0

    def _apply_hysteresis(self, target_a: float, available_w: float) -> float:
        """Hysterese wie evcc: Enable/Disable Delays als zeitlicher Filter."""
        now = time.time()

        if not self._enabled:
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
            if available_w < self.disable_threshold_w:
                if self._disable_timer is None:
                    self._disable_timer = now
                    log.debug("LP %s: Disable-Timer gestartet (%.0fW < %.0fW)",
                              self.name, available_w, self.disable_threshold_w)
                elif now - self._disable_timer >= self.disable_delay_s:
                    log.info("LP %s: PV Disable — %.0fW < %.0fW fuer %ds",
                             self.name, available_w, self.disable_threshold_w, self.disable_delay_s)
                    self._disable_timer = None
                    return 0  # Disable!
                return max(target_a, self.min_current)
            else:
                self._disable_timer = None

        return target_a

    def _set_charging(self, enable: bool, target_a: float):
        """Setzt Charger-Status — symmetrischer Watchdog + NRG-Kick-Heartbeat.

        SYMMETRISCHER WATCHDOG: in JEDEM Zyklus pruefen ob der Charger WIRKLICH
        dem Ziel-Zustand entspricht. Wenn der Real-Zustand abweicht (egal in
        welche Richtung), erneut schreiben. Behandelt sowohl unerwartetes
        Self-Enable als auch unerwartetes Self-Pause.

        NRG KICK HEARTBEAT (Sofort-Modus): NRG Kick Gen2 hat einen internen
        Session-Watchdog (~5 Min) der sich NUR durch Schreiben von Register 195
        (Pause) resettet — NICHT allein durch Register 194 (Strom). Im Sofort-
        Modus wird Reg 195 daher jeden Zyklus (10s) explizit geschrieben.

        Strom-Setpoint (Register 194) wird jeden Zyklus geschrieben (wie evcc).
        """
        try:
            actually_enabled = self.charger.enabled()
        except Exception:
            actually_enabled = None

        # Symmetrisch: schreibe wenn (a) erstmaliger Wechsel ODER
        # (b) Real-Zustand weicht in irgendeine Richtung vom Soll ab.
        mismatch = (actually_enabled is not None and actually_enabled != enable)
        need_enable_write = (enable != self._last_written_enabled) or mismatch

        if need_enable_write:
            if mismatch and self._last_written_enabled == enable:
                msg = (f"LP {self.name}: Charger-Status weicht vom Soll ab "
                       f"(Soll={'an' if enable else 'AUS'}, "
                       f"Ist={'an' if actually_enabled else 'AUS'}) — re-write")
                log.warning(msg)
                if self._db is not None:
                    try:
                        self._db.publish_log("warning", msg)
                    except Exception:
                        pass
            self.charger.enable(enable)
            self._last_written_enabled = enable
            self._enabled = enable
            self._charger_switch_time = time.time()

        # Strom-Setpoint in JEDEM Zyklus schreiben (wie evcc).
        if enable and target_a >= self.min_current:
            self.charger.max_current(target_a)
            self._last_written_current = target_a
        elif not enable:
            try:
                self.charger.max_current(self.min_current)
            except Exception:
                pass

        # Sofort-Modus Heartbeat: Reg 195 jeden Zyklus schreiben wenn kein Mismatch.
        # Verhindert den NRG Kick Gen2 Watchdog-Timeout.
        if enable and self.mode == "now" and not need_enable_write:
            try:
                self.charger.enable(True)
                log.debug("LP %s: Sofort-Heartbeat — Pause-Register refreshed", self.name)
            except Exception as e:
                log.debug("LP %s: Heartbeat fehlgeschlagen: %s", self.name, e)

        self._target_current_a = target_a
        self._enabled = enable

    def _detect_active_phases(self) -> int:
        """Erkennt aktive Phasen (evcc: > 1.0A Schwelle)."""
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
        """Session Tracking — Status-basiert wie evcc."""
        if self._status in ("B", "C") and self._charging_power_w > 50:
            if self._session is None:
                self._session = ChargingSession(self.id, self.mode, self.phases)
                if self.vehicle_soc is not None:
                    self._session.vehicle_soc_start = self.vehicle_soc
                log.info("LP %s: Ladesession gestartet", self.name)
            else:
                self._session.update(self._charging_power_w, self._last_solar_share)

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
        self._enable_timer = None
        self._disable_timer = None
        if mode in ("off", "pv"):
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
        currents = None
        voltages = None
        apparent_va = None
        power_factor = None
        active_phases = self.phases
        if isinstance(self.charger, PhaseCurrents):
            try:
                l1, l2, l3 = self.charger.currents()
                currents = [round(l1, 1), round(l2, 1), round(l3, 1)]
                active_phases = sum(1 for i in (l1, l2, l3) if i > 0.5)
                if active_phases == 0:
                    active_phases = self.phases
                if hasattr(self.charger, '_read_reg'):
                    try:
                        u1 = self.charger._read_reg("voltage_l1") or 0
                        u2 = self.charger._read_reg("voltage_l2") or 0
                        u3 = self.charger._read_reg("voltage_l3") or 0
                        if u1 > 100:
                            voltages = [round(u1, 1), round(u2, 1), round(u3, 1)]
                            apparent_va = round(u1 * l1 + u2 * l2 + u3 * l3)
                            if apparent_va > 0 and self._charging_power_w > 0:
                                power_factor = round(self._charging_power_w / apparent_va, 2)
                    except Exception:
                        pass
            except Exception:
                pass

        result = {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "status": self._status,
            "charging_power_w": round(self._charging_power_w),
            "target_current_a": round(self._target_current_a, 1),
            "phases": self.phases,
            "active_phases": active_phases,
            "currents": currents,
            "voltages": voltages,
            "apparent_va": apparent_va,
            "power_factor": power_factor,
            "enabled": self._enabled,
            "target_soc": self.target_soc,
            "min_soc": self.min_soc,
            "max_current": self.max_current,
            "cost_limit_ct": self.cost_limit_ct,
            "battery_boost": self.battery_boost,
            "zombie_wakeup_enabled": self.zombie_wakeup_enabled,
            "vehicle_soc": self.vehicle_soc,
            "battery_kwh": getattr(self, '_vehicle_battery_kwh', None),
            "soc_diagnostic": self.diagnostic_soc_state(),
        }

        if self._session:
            result["session"] = self._session.to_dict()

        return result

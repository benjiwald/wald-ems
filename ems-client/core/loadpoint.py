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
        # Aufteilung Solar/Netz nach letztem solar_share (repraesentativ fuer Intervall)
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
        # Zombie-Wake-Up: CP-Signal-Toggle wenn Status B + 0W > 5 min.
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
        # 38 kWh Ladegrenze. Kann pro Loadpoint in der Config ueberschrieben
        # werden via 'session_limit_factor' (0..1).
        self.session_limit_factor = float(config.get("session_limit_factor", 0.95))

        # Hysterese (evcc-Defaults)
        # Asymmetrische Hysterese — responsiv + Wolken-tolerant:
        # Enable: 4000W fuer 20s (reagiert schnell auf Sonne, evcc-aehnlich)
        # Disable: 0W fuer 300s (5 Min, toleriert Wolken, laedt durch mit min_current)
        # Einschalten ab dem realen Mindestbedarf der Zoe (9 A -> ~4,7 kW). Der alte
        # Default 4000 W liess den Start bis zu 0,7 kW aus dem Speicher stuetzen.
        self.enable_threshold_w = float(config.get("enable_threshold_w", 4700))
        self.enable_delay_s = int(config.get("enable_delay_s", 20))       # schnelle Reaktion
        # Unter dieser Leistung (Auto-Anteil am PV-Ueberschuss) laeuft der Abschalt-
        # Timer. 0 W (evcc-Default) passt hier nicht: der Hausspeicher verdeckt den
        # Netzbezug, PV-Modus wuerde nach dem Start bis Ueberschuss 0 aus dem
        # Speicher weiterladen. 4200 W = 0,5 kW unter der Einschaltschwelle; der
        # Abstand faengt Messrauschen ab, Wolken ueberbrueckt disable_delay_s.
        self.disable_threshold_w = float(config.get("disable_threshold_w", 4200))
        self.disable_delay_s = int(config.get("disable_delay_s", 300))    # 5 Min Wolkenpuffer

        # Reales Verhaeltnis Ladeleistung/Setpoint (W pro A), pro Fahrzeug gelernt.
        # Nominal 230 V x Phasen; die Zoe liegt wegen ihres Leistungsfaktors bei
        # ~520 W/A (9 A) bis ~625 W/A (16 A) statt 690. Siehe _learn_w_per_a().
        self._nominal_w_per_a: float = VOLTAGE * self.phases
        self._w_per_a: float = self._nominal_w_per_a
        self._w_per_a_by_vehicle: dict[str, float] = {}
        self._w_per_a_persisted: dict[str, float] = {}
        self._w_per_a_restored = False
        self._ratio_vehicle_key: str | None = None
        self._ratio_targets: list[float] = []

        self.charger = charger
        self.meter = meter

        # Zustandsvariablen
        self._status = "A"
        self._prev_status = "A"
        self._charging_power_w = 0.0
        self._target_current_a = 0.0
        self._enabled = False
        self._last_solar_share: float = 1.0  # Anteil aus PV/Batterie

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
        # also NIE auf, weil Cloud-SoC ≤ Realitaet bleibt.
        #
        # Loesung: Beim Plug-in (A→B) snapshoten wir die SoC als Baseline.
        # Die Wallbox-Energie wird ueber die GESAMTE Session aufaddiert
        # (monotonisch, ohne Reset bei API-Updates). Estimated berechnet:
        #
        #   estimated = max(api_soc, session_start_soc + delivered/battery*100)
        #              ↑                                    ↑
        #   Cloud als untere Grenze              Monoton aus Wallbox-Energie
        #
        # → Wenn die Cloud hinterher ist, gewinnt der Wallbox-Term.
        # → Wenn die Cloud mal voraus ist, gewinnt der Cloud-Wert.
        # → Estimated nimmt nie ab solange die Session laeuft.
        self._vehicle_soc_api: float | None = None      # letzter Cloud-Wert
        self._vehicle_soc_api_ts: float = 0.0           # wann
        self._vehicle_battery_kwh: float = 0.0          # vom main.py
        self._session_start_soc: float | None = None    # Snapshot beim Plug-in
        # Letzter API-Wert, der bereits als Anker "verbraucht" wurde (v1.9.3).
        # Erkennt ob ein neu eintreffender API-Wert eine ECHTE Aenderung ist
        # (→ Session-Anker neu setzen, Drift korrigieren) oder nur eine
        # Wiederholung des gleichen gecachten Werts (→ nichts tun, weiter
        # ueber Wallbox-Energie schaetzen, Cloud-Lag ueberbruecken).
        self._last_api_soc_for_anchor: float | None = None
        self._session_delivered_wh: float = 0.0         # Σ Wallbox-Energie der Session
        self._last_estimation_ts: float = 0.0           # für dt-Berechnung
        # _prev_status hier NICHT neu initialisieren (war oben schon "A")
        # Service-Restart-Detection: erster poll erkennt B/C → keine Plug-in-Reset
        self._estimation_initialized: bool = False
        # DB-Persistenz: alle 30s in state-Tabelle (ueberlebt Service-Restart)
        self._last_persist_ts: float = 0.0
        # Plug-out-Hysterese (v1.8.6): Modbus-Glitches die kurz Status A
        # melden duerfen NICHT die Session zerstoeren. Erst nach 60s
        # konstantem A-Status zaehlt's als echtes Abstecken.
        self._status_a_first_seen: float | None = None
        self._prev_status_effective: str = "A"  # entkoppelt von _prev_status

        # Aktives Fahrzeug am Loadpoint (v1.9.0): "default" = konfiguriertes
        # Fahrzeug aus YAML (z.B. Zoe), "guest" = Fremdfahrzeug ohne SoC-
        # Tracking. Beim Plug-out wird automatisch auf "default" zurueck-
        # gesetzt damit man die Auswahl nicht stehen laesst.
        self._active_vehicle: str = "default"

        # DB-Handle (optional, vom main.py via attach_db gesetzt) — fuer
        # ausgewaehlte Events (Target-SoC-Stop) zusaetzlich in die UI-Logs.
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
        der schon gelaufenen Wallbox-Energie:
            session_start_soc = api - delivered / battery * 100
        Damit gewinnt die Estimation auch bei verspaeteter Erst-API zurueck.
        """
        # SANITY-FILTER — implausible Werte ignorieren.
        # Hintergrund: die Renault Kamereon API liefert gelegentlich 0% wenn
        # das Auto schlaeft oder bei Auth-Aussetzern. Frueher haben wir das
        # blind als "Auto leer" akzeptiert → Estimator dachte SoC=0% →
        # Stop-Logic greift nie → Auto laedt auf 100% (Auto-BMS stoppt).
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
        # Plausible Drops > 30% vom letzten Wert sind verdaechtig (API-Aussetzer,
        # nicht das echte Auto). Wir loggen + ignorieren, ausser der bisherige
        # Wert ist sehr alt (> 30 min).
        prev = self._vehicle_soc_api
        if prev is not None and (prev - soc_f) > 30:
            api_age = time.time() - self._vehicle_soc_api_ts
            if api_age < 1800:
                log.warning("LP %s: API SoC-Drop %.0f%% → %.0f%% verdaechtig (Alter %.0fs) — ignoriert",
                            self.name, prev, soc_f, api_age)
                return

        is_first_value = self._vehicle_soc_api is None
        # Estimation VOR dem Ueberschreiben von _vehicle_soc_api festhalten —
        # nur so zeigt der Drift-Log unten den tatsaechlichen "Vorher"-Wert,
        # unabhaengig davon ob der neue API-Wert hoeher oder niedriger liegt.
        pre_update_estimate = self.vehicle_soc
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

        # Recenter-on-fresh-value (v1.9.3): Sobald ein NEUER, tatsaechlich
        # veraenderter API-Wert eintrifft (nicht nur eine Wiederholung des
        # gleichen gecachten Stands), setzen wir den Session-Anker neu.
        #
        # Hintergrund (live beobachtet am 2026-07-08): die Formel
        # session_start + delivered/battery*100 geht von 100% Ladeeffizienz
        # aus. Reale Effizienz liegt oft niedriger — besonders Richtung
        # hoher SoC (CV-Phase/BMS-Balancing, mehr Waermeverlust). Beispiel:
        # 8.87 kWh geliefert liessen die Schaetzung um 21.7 Punkte steigen,
        # real waren es nur 16 Punkte (76%→92%) → 6.2 Punkte Drift nach oben.
        # Weil vehicle_soc = max(api, estimate) niemals nach UNTEN korrigiert,
        # blieb der Fehler fuer den Rest der Session bestehen.
        #
        # Fix: Bei jeder ECHTEN Aenderung des API-Werts (>= 1 Punkt seit dem
        # letzten Anker) wird der Anker komplett neu gesetzt (Session-Start =
        # neuer API-Wert, delivered = 0). Zwischen zwei echten API-Updates
        # ueberbrueckt die Wallbox-Energie weiterhin Cloud-Lag wie bisher —
        # nur wird der Fehler jetzt spaetestens beim naechsten echten Update
        # wieder auf 0 zurueckgesetzt, statt sich über Stunden aufzusummieren.
        elif (self._prev_status in ("B", "C")
                and self._session_start_soc is not None
                and self._last_api_soc_for_anchor is not None
                and abs(soc_f - self._last_api_soc_for_anchor) >= 1.0):
            old_estimate = pre_update_estimate if pre_update_estimate is not None else soc_f
            drift = old_estimate - soc_f
            self._session_start_soc = soc_f
            self._session_delivered_wh = 0.0
            msg = (f"LP {self.name}: SoC-Anker neu gesetzt auf frischen API-Wert "
                   f"{soc_f:.0f}% (Schaetzung war {old_estimate:.1f}%, Drift {drift:+.1f} Punkte)")
            log.info(msg)
            if self._db is not None and abs(drift) >= 1.0:
                try:
                    self._db.publish_log("warning" if abs(drift) >= 3.0 else "info", msg)
                except Exception:
                    pass
            self._persist_session_state()

        # Anker-Vergleichswert immer aktualisieren — sonst wird der naechste
        # echte Sprung nicht als "neu" erkannt.
        self._last_api_soc_for_anchor = soc_f

    @property
    def vehicle_soc(self) -> float | None:
        """Estimated SoC — robust gegen Cloud-Lag.

        Reihenfolge:
        1. Wenn session_start + battery_kwh bekannt:
             max(api, session_start + delivered/battery*100)
        2. Wenn nur api da (Session-Start fehlt aber Energie ist geflossen):
             api + delivered/battery*100  ← konservativer Fallback statt nur api,
             damit wir bei verlorenem Start-SoC nicht ewig weiterladen
        3. Sonst api.
        """
        api = self._vehicle_soc_api
        if api is None:
            # Wenn die API noch nichts geliefert hat, koennen wir nichts ueber
            # SoC sagen. Stop-Logic greift dann nicht — Charger laeuft via
            # PV-Modus Hysterese normal weiter.
            return None
        if self._vehicle_battery_kwh and self._vehicle_battery_kwh > 0:
            delta_pct = (self._session_delivered_wh / 1000.0) \
                        / self._vehicle_battery_kwh * 100.0
            if self._session_start_soc is not None:
                # Echter Start bekannt
                estimated_from_session = self._session_start_soc + delta_pct
                return max(0.0, min(100.0, max(api, estimated_from_session)))
            elif delta_pct > 0.5:
                # Session-Start unbekannt aber Energie ist geflossen
                # → konservativ api + delta verwenden (sonst werden wir nie
                # stoppen wenn Cloud immer hinterher ist)
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
        """Speichert Session-State in DB. Wird alle 30s aufgerufen."""
        if self._db is None:
            return
        try:
            self._db.set_state(self._session_state_key(), {
                "start_soc": self._session_start_soc,
                "delivered_wh": self._session_delivered_wh,
                "active_vehicle": self._active_vehicle,
                # last_anchor_soc mitpersistieren (v1.9.3): sonst wuerde nach
                # einem Service-Restart der naechste API-Wert nie als "neu"
                # erkannt (weil None), und ein zwischenzeitlich gewachsener
                # Drift bliebe bis zum UEBERNAECHSTEN API-Update bestehen.
                "last_anchor_soc": self._last_api_soc_for_anchor,
                "ts": time.time(),
            })
        except Exception as e:
            log.debug("LP %s: _persist_session_state Fehler: %s", self.name, e)

    def _restore_session_state(self) -> bool:
        """Versucht Session-State aus DB zu laden. Returns True wenn erfolgreich.

        Wird beim ersten poll-Tick nach Service-Restart aufgerufen, wenn der
        Charger-Status bereits B/C ist (= Session war schon im Gange).

        Sanity: kaputter State (start_soc=0 mit delivered=0) wird ignoriert —
        das ist nicht wiederherstellbar und sollte komplett neu lazy-init werden.
        """
        if self._db is None:
            return False
        try:
            data = self._db.get_state(self._session_state_key())
            if not isinstance(data, dict):
                return False
            ts = data.get("ts", 0)
            # Alte State (>2h) verwerfen — wahrscheinlich neue Session
            if time.time() - ts > 7200:
                return False
            start_soc = data.get("start_soc")
            delivered_wh = float(data.get("delivered_wh", 0))
            # Kaputten State erkennen: start_soc <= 0.5 ist nicht plausibel
            # (eine Zoe-Session startet nie bei "echten" 0%). Wenn delivered
            # zudem klein → sicher Bullshit-Restoration verhindern.
            if start_soc is not None and start_soc < 1.0:
                log.warning("LP %s: DB-Session-State hat invaliden start_soc=%.1f%% — verworfen",
                            self.name, start_soc)
                return False
            self._session_start_soc = start_soc
            self._session_delivered_wh = delivered_wh
            # Auch active_vehicle aus DB wiederherstellen (falls vor Restart "guest" war)
            av = data.get("active_vehicle")
            if av in ("default", "guest"):
                self._active_vehicle = av
            # last_anchor_soc wiederherstellen (v1.9.3) — sonst greift die
            # Drift-Korrektur beim ersten API-Update nach dem Restart nicht.
            last_anchor = data.get("last_anchor_soc")
            if isinstance(last_anchor, (int, float)):
                self._last_api_soc_for_anchor = float(last_anchor)
            return True
        except Exception as e:
            log.debug("LP %s: _restore_session_state Fehler: %s", self.name, e)
            return False

    def _clear_session_state(self) -> None:
        """Loescht Session-State aus DB. Beim Plug-out."""
        if self._db is None:
            return
        try:
            self._db.set_state(self._session_state_key(), None)
        except Exception:
            pass

    def force_reset_session_estimation(self) -> dict:
        """Reset des SoC-Estimation-States. Behält Cloud-API-Wert.

        Verwendet wenn die Lazy-Init mit invaliden Werten arbeitet (z.B.
        Renault-API liefert temporaer 0%). Nach Reset wird beim naechsten
        validen API-Update neu lazy-init oder beim Plug-out komplett verworfen.
        """
        before = self.diagnostic_soc_state()
        self._session_start_soc = None
        self._session_delivered_wh = 0.0
        self._last_api_soc_for_anchor = None
        self._estimation_initialized = False
        self._clear_session_state()
        log.info("LP %s: SoC-Estimation force-reset (war: %s)", self.name, before)
        if self._db is not None:
            try:
                self._db.publish_log("info",
                    f"LP {self.name}: SoC-Estimation force-reset (war session_start={before.get('session_start_soc')}, "
                    f"delivered={before.get('session_delivered_kwh')} kWh)")
            except Exception:
                pass
        return before

    def diagnostic_soc_state(self) -> dict:
        """Aktuelle SoC-Estimation Variablen fuer Debugging."""
        # Dynamischer kWh-Cap je nach SoC-Verfuegbarkeit
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
            "last_anchor_soc": self._last_api_soc_for_anchor,
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
                          die Batterie aggressiv leerzieht. Default: available_w
                          (Backwards-kompatibel falls nicht uebergeben).

        Returns:
            Tatsächlich genutzter Strom in Watt
        """
        if pv_surplus_w is None:
            pv_surplus_w = available_w
        # 1. Charger-Status lesen
        self._status = self.charger.status()

        # Zombie-Schutz nach Restart: Wenn der Charger beim (Re)Start bereits
        # geladen hat, _ever_enabled setzen — sonst greift der Disable-Pfad
        # (elif self._ever_enabled) nie und der Charger läuft unkontrolliert weiter.
        if self._status == "C" and not self._ever_enabled:
            self._ever_enabled = True
            # Laufenden Zustand uebernehmen. Sonst startet die PV-Hysterese bei "aus",
            # update() pausiert die Wallbox fuer mindestens enable_delay_s, und die
            # Zoe muss neu anlaufen (tut sie nicht immer von selbst). Abschalten
            # uebernimmt danach regulaer die Hysterese bzw. der Modus.
            self._enabled = True
            self._last_written_enabled = True
            log.info("LP %s: Charger beim Start bereits aktiv — Ladezustand uebernommen", self.name)

        # 2. Aktuelle Ladeleistung messen
        if self._status == "A":
            self._charging_power_w = 0
        elif isinstance(self.charger, Meter):
            self._charging_power_w = abs(self.charger.current_power())
        elif self.meter:
            self._charging_power_w = abs(self.meter.current_power())
        else:
            self._charging_power_w = 0

        self._learn_w_per_a()

        # SoC-Estimation auf Session-Basis (evcc-style soc.estimate):
        # Beim Plug-in (A→B) snapshoten wir die SoC als Baseline. Wallbox-
        # Energie wird monoton ueber die ganze Session aufaddiert — auch
        # ueber Pausen hinweg, NICHT bei API-Updates resettet. Beim Plug-out
        # (→A) wird die Session zurueckgesetzt.
        now_ts = time.time()
        if self._last_estimation_ts == 0:
            self._last_estimation_ts = now_ts

        # SERVICE-RESTART-DETECTION: wenn der allererste poll bereits Status
        # B/C zeigt, ist das KEIN Plug-in — der Service ist mitten in einer
        # laufenden Session neugestartet. Aus der DB rekonstruieren wenn
        # vorhanden, sonst lazy-init via Renault-API beim naechsten Update.
        if not self._estimation_initialized:
            self._estimation_initialized = True
            if self._status in ("B", "C"):
                # Versuch DB-Recovery (ueberlebt Service-Restart)
                restored = self._restore_session_state()
                if restored:
                    log.info("LP %s: Service-Restart waehrend laufender Session erkannt "
                             "(Status %s) — Session-State aus DB wiederhergestellt: "
                             "start_soc=%s, delivered=%.2f kWh",
                             self.name, self._status,
                             f"{self._session_start_soc:.0f}%" if self._session_start_soc is not None else "—",
                             self._session_delivered_wh / 1000.0)
                    if self._db is not None:
                        try:
                            self._db.publish_log("info",
                                f"LP {self.name}: Service-Restart waehrend Session — "
                                f"State wiederhergestellt: start={self._session_start_soc}, "
                                f"delivered={self._session_delivered_wh/1000.0:.2f} kWh")
                        except Exception:
                            pass
                else:
                    log.warning("LP %s: Service-Restart waehrend laufender Session (Status %s) "
                                "— kein State in DB, lazy-init beim naechsten API-Update",
                                self.name, self._status)
                # _prev_status auf aktuellen Wert setzen damit unten NICHT
                # "Plug-in detektiert" feuert
                self._prev_status = self._status
                self._prev_status_effective = self._status

        # Plug-out-Hysterese (v1.8.6): Status A muss seit > PLUG_OUT_HYST_S
        # konstant gemeldet werden, sonst ignorieren. Sonst zerstoert ein
        # einziger Modbus-Glitch (Wallbox-Watchdog-Reset, Read-Fehler, kurzer
        # B→A Übergang) den Session-Start-SoC. Genau das ist am 02.06. um
        # 10:50 passiert — Status kurz auf A, dann zurueck B/C, und der
        # falsche neue Session-Start war 65% statt 30%.
        PLUG_OUT_HYST_S = 60.0
        if self._status == "A":
            # Erste Sichtung von A → Timer starten
            if self._status_a_first_seen is None:
                self._status_a_first_seen = now_ts
        else:
            # Status zurueck auf B/C → Timer reset (war nur Glitch)
            if self._status_a_first_seen is not None:
                glitch_dur = now_ts - self._status_a_first_seen
                if glitch_dur < PLUG_OUT_HYST_S:
                    log.info("LP %s: Status-A-Glitch ignoriert (%.1fs) — Session bleibt",
                             self.name, glitch_dur)
            self._status_a_first_seen = None

        # Confirmed A: Status A laenger als Hysterese-Schwelle
        confirmed_a = (self._status_a_first_seen is not None and
                       (now_ts - self._status_a_first_seen) >= PLUG_OUT_HYST_S)
        # Effektiver Status fuer Plug-Detection: A nur wenn confirmed
        effective_status = "A" if confirmed_a else (self._status if self._status != "A" else self._prev_status_effective)

        # A → B/C: Plug-in detektiert. Snapshot SoC als Session-Start.
        if self._prev_status_effective == "A" and effective_status in ("B", "C"):
            self._session_start_soc = self._vehicle_soc_api
            self._session_delivered_wh = 0.0
            # Anker-Vergleichswert synchron mit dem neuen Session-Start setzen,
            # sonst wuerde der naechste API-Poll (mit demselben Wert) sofort
            # einen unnoetigen "SoC-Anker neu gesetzt"-Log ausloesen.
            self._last_api_soc_for_anchor = self._session_start_soc
            log.info("LP %s: Plug-in detektiert (Status %s→%s) — Session-Start SoC = %s",
                     self.name, self._prev_status_effective, effective_status,
                     f"{self._session_start_soc:.0f}%" if self._session_start_soc is not None else "—")
            if self._db is not None:
                try:
                    self._db.publish_log("info",
                        f"LP {self.name}: Plug-in — Session-Start SoC = "
                        f"{'%.0f%%' % self._session_start_soc if self._session_start_soc is not None else 'unbekannt (lazy-init bei naechstem API-Update)'}")
                except Exception:
                    pass

        # → A: Plug-out detektiert (nur confirmed). Session beenden + DB-State loeschen.
        if self._prev_status_effective != "A" and effective_status == "A":
            log.info("LP %s: Plug-out detektiert (confirmed nach %.0fs) — Session-Energy %.2f kWh",
                     self.name, PLUG_OUT_HYST_S, self._session_delivered_wh / 1000.0)
            self._session_start_soc = None
            self._session_delivered_wh = 0.0
            self._last_api_soc_for_anchor = None
            self._clear_session_state()
            # Beim Plug-out: Gast-Auswahl automatisch zuruecksetzen,
            # damit beim naechsten Anstecken wieder das Standard-Fahrzeug
            # gilt — sonst bleibt "Gast" stehen und Zoe laedt unkontrolliert.
            if self._active_vehicle == "guest":
                log.info("LP %s: Aktives Fahrzeug → default (Reset bei Plug-out)", self.name)
                self._active_vehicle = "default"
                if self._db is not None:
                    try:
                        self._db.publish_log("info",
                            f"LP {self.name}: Gast-Auswahl zurueckgesetzt (Plug-out)")
                    except Exception:
                        pass

        # Wallbox-Energie zur Session aufaddieren — laufende Summe, nie reset
        # waehrend die Session aktiv ist.
        dt = now_ts - self._last_estimation_ts
        # dt > 600 = Aussetzer (Service-Restart, Sleep). In dem Fall wissen wir
        # nicht wieviel waehrenddessen geflossen ist → konservativ NICHT
        # aufaddieren. Beim naechsten Tick laeuft es normal weiter.
        if 0 < dt < 600 and self._charging_power_w > 50 and self._status in ("B", "C"):
            self._session_delivered_wh += self._charging_power_w * (dt / 3600.0)
        self._last_estimation_ts = now_ts
        self._prev_status = self._status
        self._prev_status_effective = effective_status

        # Session-State alle 30s in DB persistieren (ueberlebt Restart)
        if self._status in ("B", "C") and (now_ts - self._last_persist_ts) > 30:
            self._persist_session_state()
            self._last_persist_ts = now_ts

        # Solar-Anteil berechnen: wenn Netz importiert, dann LP teils aus Netz
        # grid_import_w > 0 = Netzbezug
        # LP Solar-Share = max(0, 1 - grid_import / lp_power)
        if self._charging_power_w > 50 and grid_import_w > 0:
            grid_share = min(1.0, grid_import_w / self._charging_power_w)
            self._last_solar_share = max(0.0, 1.0 - grid_share)
        else:
            self._last_solar_share = 1.0  # PV-Überschuss / Netzeinspeisung → 100% Solar

        # 3. Session aktualisieren (Status-basiert wie evcc)
        self._update_session()

        # 3a. Zombie-Wake-Up (evcc-Style):
        # Wenn enabled+verbunden (B) seit >5 min und kein Strom fliesst,
        # Fahrzeug ist wahrscheinlich eingeschlafen. Pause-Register
        # togglen (1s aus, dann wieder an) triggert neuen CP-Signal-Wechsel.
        # ACHTUNG: Renault Zoe verliert dadurch die Session — fuer Renault
        # ist zombie_wakeup_enabled per Default False.
        now = time.time()
        # Timeout modusabhaengig: now 60s (Sofort soll sofort laden), pv/min_pv 300s
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
                    # _last_written_enabled bleibt True (Ziel-Status)
                    self._last_wake_up = now
                    self._zombie_since = None
                except Exception as e:
                    log.error("LP %s: Wake-Up fehlgeschlagen: %s", self.name, e)
        else:
            self._zombie_since = None  # zieht Strom oder Wake-Up deaktiviert → reset

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
        #        (Backup wenn SoC-Estimation kaputt ist, z.B. Renault-Cloud-Lag,
        #         lost session_start, etc.)
        #
        # WICHTIG: wenn keine SoC-Daten verfuegbar sind (z.B. Renault-Login
        # kaputt → api_soc=None), wird der kWh-Cap aggressiver. Sonst wuerde
        # das Auto bis 95% gefuettert werden ohne jede Kontrolle.
        # Dynamischer Cap:
        #   mit valider SoC → battery_kwh * session_limit_factor
        #   ohne SoC        → battery_kwh * target_soc/100 * 1.0  (target wird ziemlich
        #                     genau erreicht, plus 10% Effizienzschwund)
        est_soc = self.vehicle_soc
        api_soc = self._vehicle_soc_api
        start_soc = self._session_start_soc
        delivered_kwh = self._session_delivered_wh / 1000.0

        # GAST-FAHRZEUG: keine SoC-/Energy-Limits — laedt bis Stecker raus.
        # User hat manuell "Gastfahrzeug" gewaehlt → wir wissen nichts ueber
        # Akku-Groesse, SoC, Ziel. Lademodus (PV/Min+PV/Now) regelt weiterhin
        # die Leistung; nur die SoC-Stop-Logik wird komplett uebersprungen.
        if self._active_vehicle == "guest":
            soc_stop = False
            kwh_cap = None
            energy_stop = False
        else:
            soc_stop = (est_soc is not None and est_soc > 0
                        and est_soc >= self.target_soc
                        and self.mode in ("pv", "min_pv", "now"))

            if self._vehicle_battery_kwh > 0:
                if est_soc is None:
                    # KEINE SoC-Daten: konservativ — Annahme worst-case Start bei
                    # 20% SoC (typisch wenn man zu Hause ankommt). target_soc/100
                    # ergibt die "max sinnvolle" Lademenge plus Effizienz-Buffer.
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
            # Stop-Log in DB: beim Uebergang Laden→Stop UND danach max. 1×/h,
            # damit man auch nach Service-Restart noch was sieht und Diagnose
            # moeglich ist falls ein Stop "haengenbleibt".
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

        # 7. Hysterese im PV-Modus — gleicher Wert wie in _calculate_target
        # (min(pv_surplus_w, available_w)), sonst wuerde der Enable/Disable-
        # Schwellwert-Check den ev_priority-Slider ignorieren, obwohl der
        # Zielstrom selbst ihn schon berücksichtigt (Inkonsistenz).
        if self.mode == "pv":
            hysteresis_basis_w = pv_surplus_w if pv_surplus_w is not None else available_w
            hysteresis_basis_w = min(hysteresis_basis_w, available_w)
            target_a = self._apply_hysteresis(target_a, hysteresis_basis_w)

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

        used_w = target_a * self._w_per_a if should_enable else 0
        log.info(
            "LP %s: mode=%s status=%s target=%.1fA power=%.0fW available=%.0fW phases=%d/%d soc=%s",
            self.name, self.mode, self._status, target_a,
            self._charging_power_w, available_w, active_phases, self.phases,
            f"{self.vehicle_soc:.0f}%" if self.vehicle_soc is not None else "—",
        )
        return used_w

    def _w_per_a_state_key(self) -> str:
        return f"lp_w_per_a_{self.name}"

    def _learn_w_per_a(self) -> None:
        """Lernt, wie viel Watt das Fahrzeug real pro Ampere Setpoint zieht.

        Damit entspricht die reale Ladeleistung dem Anteil, den der
        PV-Priorisierungs-Slider dem Auto zuteilt. Gelernt wird nur im
        eingeschwungenen Ladebetrieb; Gastfahrzeuge werden nicht persistiert.
        """
        key = getattr(self, "_active_vehicle", "default") or "default"
        if not self._w_per_a_restored and self._db is not None:
            self._w_per_a_restored = True
            try:
                stored = self._db.get_state(self._w_per_a_state_key()) or {}
                for k, v in stored.items():
                    self._w_per_a_by_vehicle[k] = float(v)
                    self._w_per_a_persisted[k] = float(v)
            except Exception as e:
                log.debug("LP %s: W/A-Restore fehlgeschlagen: %s", self.name, e)
            self._ratio_vehicle_key = None
        if key != self._ratio_vehicle_key:
            self._ratio_vehicle_key = key
            self._w_per_a = self._w_per_a_by_vehicle.get(key, self._nominal_w_per_a)
            self._ratio_targets = []
            log.info("LP %s: W/A fuer Fahrzeug '%s' = %.0f", self.name, key, self._w_per_a)

        soc = self.vehicle_soc
        steady = (self._status == "C" and self._last_written_enabled
                  and self._target_current_a >= self.min_current
                  and self._charging_power_w >= 1000
                  and not (soc is not None and soc >= 88))  # Lade-Taper nicht lernen
        if not steady:
            self._ratio_targets = []
            return
        self._ratio_targets = (self._ratio_targets + [self._target_current_a])[-3:]
        if len(self._ratio_targets) < 3 or max(self._ratio_targets) - min(self._ratio_targets) > 1.0:
            return  # Setpoint noch in Bewegung, Messwert passt nicht sicher dazu

        sample = self._charging_power_w / self._target_current_a
        sample = max(0.5 * self._nominal_w_per_a, min(1.05 * self._nominal_w_per_a, sample))
        self._w_per_a = 0.7 * self._w_per_a + 0.3 * sample
        self._w_per_a_by_vehicle[key] = self._w_per_a
        last = self._w_per_a_persisted.get(key, 0.0)
        if key != "guest" and self._db is not None and abs(self._w_per_a - last) > 0.02 * self._w_per_a:
            try:
                self._w_per_a_persisted[key] = self._w_per_a
                self._db.set_state(self._w_per_a_state_key(),
                                   {k: round(v, 1) for k, v in self._w_per_a_persisted.items()})
            except Exception as e:
                log.debug("LP %s: W/A-Persist fehlgeschlagen: %s", self.name, e)

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
            # PV-Modus (v1.10.2): nutzt jetzt pv_surplus_w (fraction-adjusted
            # via ev_priority_pct aus site.py) statt available_w allein.
            #
            # VORHER: available_w = surplus_w + battery_redirect_w, wobei
            # surplus_w ueber current_lp_power einen Regelkreis bildet, der
            # IMMER gegen "Batterie bekommt nur den Puffer-Rest" konvergiert
            # — der ev_priority-Schieberegler hatte im pv-Modus dadurch NUR
            # einen abgeschwaechten, nicht-proportionalen Effekt (siehe
            # Kommentar zu battery_redirect_w in site.py). Live beobachtet
            # 2026-07-11: Slider auf 50% ("6,5 kW angeboten"), Auto lud
            # trotzdem mit 9,6 kW — der pv-Modus ignorierte den Slider quasi.
            #
            # JETZT: min(pv_surplus_w, available_w) — pv_surplus_w liefert
            # die saubere proportionale Aufteilung (siehe Kommentar bei
            # min_pv unten), available_w bleibt als Deckel fuer den
            # Netzanschluss (grid_headroom_w) und den 100%-Fallback (falls
            # pv_surplus_w aus irgendeinem Grund fehlt) erhalten.
            surplus_w = pv_surplus_w if pv_surplus_w is not None else available_w
            capped_w = min(surplus_w, available_w)
            return capped_w / self._w_per_a

        if self.mode == "min_pv":
            # Min+PV: min_current als Untergrenze, max_current wenn echter PV-Surplus.
            # WICHTIG: pv_surplus_w (= PV minus Hausgrundlast) statt available_w,
            # damit nicht aggressiv die Batterie entladen wird wenn PV niedrig ist.
            surplus_w = pv_surplus_w if pv_surplus_w is not None else available_w
            return max(self.min_current, surplus_w / self._w_per_a)

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
                    # Mindestens min_current: die Hysterese entscheidet ueber an/aus.
                    # Ein roher Wert unter Minimum wurde in update() sofort wieder
                    # genullt -> Freigabe blieb nie stehen (Endlosschleife 20.09.2026).
                    return max(target_a, self.min_current)  # Enable!
                return 0  # Noch warten
            else:
                self._enable_timer = None
                return 0  # Unter Threshold
        else:
            # Bereits aktiv → Disable nur bei echtem Netzbezug (evcc-Style)
            # Bei kurzen Wolken weiterladen mit min_current statt abzubrechen
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
                # Timer laeuft: weiterladen mit min_current (aus Netz/Batterie)
                return max(target_a, self.min_current)
            else:
                self._disable_timer = None

        # Aktiv und nicht im Abschalt-Fenster: nie unter min_current fallen,
        # sonst schaltet update() sofort ab und umgeht disable_delay_s (Pendeln).
        return max(target_a, self.min_current)

    def _set_charging(self, enable: bool, target_a: float):
        """Setzt Charger-Status — Read-before-write fuer Watchdog-Recovery.

        SYMMETRISCHER Watchdog (v1.8.6): in JEDEM Zyklus pruefen ob der
        Charger WIRKLICH dem Ziel-Zustand entspricht. Wenn der Real-Zustand
        vom Soll-Zustand abweicht (egal in welche Richtung), erneut schreiben.

        Bug vor v1.8.6: Stop wurde nur EINMAL geschrieben (bei Wechsel
        enable=True→False). Wenn der Charger sich danach von alleine wieder
        anschaltete (z.B. nach Auto-Wake-Signal vom EV oder NRG-Kick-Watchdog
        Reset), wurde der Stop NIE erneut gesendet → das EMS dachte "ich hab
        ja schon gestoppt" und das Auto lud weiter auf 100%.

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
                # Wir hatten schon enable=X geschrieben, Charger sagt aber nicht-X.
                # Das ist genau der Bug-Modus → laut warnen + DB-Log.
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
        # BEI DISABLE auch Setpoint auf 0 fahren — manche Wallboxen ignorieren
        # das Pause-Register wenn der Setpoint hoch bleibt.
        if enable and target_a >= self.min_current:
            self.charger.max_current(target_a)
            self._last_written_current = target_a
        elif not enable:
            # Defensiv: bei jedem Stop-Tick auch Setpoint auf min_current
            # zurueckziehen (manche Charger laden bei hohem Setpoint trotz
            # pause-bit weiter).
            try:
                self.charger.max_current(self.min_current)
            except Exception:
                pass

        # Sofort-Modus Heartbeat (Wald EMS v1.0.41): Pause-Register (Reg 195) jeden
        # Zyklus schreiben. Der NRG Kick Gen2 pausierte beim Bruder-Setup sonst alle
        # ~5 Minuten; Reg 194 (Strom) allein setzt dessen Session-Timer nicht zurueck.
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
                self._session.update(self._charging_power_w, self._last_solar_share)

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

    def set_active_vehicle(self, vehicle_id: str):
        """Setzt das aktive Fahrzeug am Loadpoint.

        Werte:
          "default" — konfiguriertes Fahrzeug aus YAML (volle SoC-Tracking)
          "guest"   — Gastfahrzeug, kein SoC, kein Target, laedt bis Stecker raus
        """
        if vehicle_id not in ("default", "guest"):
            log.warning("LP %s: set_active_vehicle('%s') ignoriert (nur 'default'/'guest')",
                        self.name, vehicle_id)
            return
        if vehicle_id == self._active_vehicle:
            return
        log.info("LP %s: Aktives Fahrzeug → %s", self.name, vehicle_id)
        self._active_vehicle = vehicle_id
        # Beim Wechsel: Session-Tracking zuruecksetzen — sonst bleibt z.B.
        # der Zoe-Session-Start fuer den Gast aktiv (oder umgekehrt).
        self._session_start_soc = None
        self._session_delivered_wh = 0.0
        self._clear_session_state()
        if self._db is not None:
            try:
                msg = ("Fahrzeug → Gastfahrzeug (laedt ohne SoC-Limit)"
                       if vehicle_id == "guest"
                       else "Fahrzeug → Standard (mit SoC-Limit)")
                self._db.publish_log("info", f"LP {self.name}: {msg}")
            except Exception:
                pass

    def pop_completed_sessions(self) -> list[dict]:
        sessions = self._completed_sessions.copy()
        self._completed_sessions.clear()
        return sessions

    def state(self) -> dict:
        # Live-Phasenströme lesen wenn Charger es unterstützt
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
                # Spannungen lesen (NRG Kick hat Register 217-219)
                if hasattr(self.charger, '_read_reg'):
                    try:
                        u1 = self.charger._read_reg("voltage_l1") or 0
                        u2 = self.charger._read_reg("voltage_l2") or 0
                        u3 = self.charger._read_reg("voltage_l3") or 0
                        if u1 > 100:  # plausible
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
            "phases": self.phases,               # Config
            "active_phases": active_phases,      # Live gemessen
            "currents": currents,                # [L1, L2, L3] in Ampere
            "voltages": voltages,                # [L1, L2, L3] in Volt
            "apparent_va": apparent_va,          # Scheinleistung VA
            "power_factor": power_factor,        # cos phi
            "enabled": self._enabled,
            "target_soc": self.target_soc,
            "min_soc": self.min_soc,
            "max_current": self.max_current,
            "cost_limit_ct": self.cost_limit_ct,
            "battery_boost": self.battery_boost,
            "zombie_wakeup_enabled": self.zombie_wakeup_enabled,
            "w_per_a": round(self._w_per_a),
            "vehicle_soc": self.vehicle_soc,
            "battery_kwh": getattr(self, '_vehicle_battery_kwh', None),
            "active_vehicle": self._active_vehicle,
            "soc_diagnostic": self.diagnostic_soc_state(),
        }

        if self._session:
            result["session"] = self._session.to_dict()

        return result

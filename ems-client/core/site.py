"""Site — Energiebilanz und Geräte-Koordination.

Berechnet alle 30 Sekunden:
- Grid Power (Netzbezug/Einspeisung)
- PV Power (Solarproduktion)
- Battery Power + SoC
- Consumption (AC Verbrauch)
- Available Power (für Loadpoints verfügbar)
"""

import logging
from typing import Any

from api.meter import Meter
from api.battery import Battery
from api.charger import Charger
from core.circuit import CircuitManager

log = logging.getLogger("ems.site")


class Site:
    """Repräsentiert einen Kundenstandort mit Energiebilanz."""

    def __init__(self, config: dict):
        self.grid_limit_w: float = (config.get("grid_limit_kw") or 11.0) * 1000
        self.buffer_w: float = config.get("buffer_w") or 100
        self.priority_soc: float = config.get("priority_soc") or 0

        # PV-Priorisierung Auto vs. Speicher (v1.10). 0.0-1.0, Default 1.0
        # (= 100% Auto-Prioritaet, entspricht dem bisherigen fest verdrahteten
        # "Auto-vor-Batterie"-Verhalten). Wird auf den unabhaengig gemessenen
        # pv_surplus_w-Pool angewendet BEVOR er an die Loadpoints geht — nicht
        # auf battery_redirect_w (selbst-referenzierende Groesse, siehe unten
        # in update() fuer Details wieso das ein Unterschied ist).
        raw_pct = config.get("ev_priority_pct", 100)
        self.ev_priority_fraction: float = max(0.0, min(100.0, float(raw_pct))) / 100.0

        # evcc nutzt keine EWMA-Glättung — Enable/Disable Delays reichen als Filter
        self.buffer_soc: float = config.get("buffer_soc") or 0

        # Tarife
        self.grid_price_eur_kwh: float = config.get("grid_price_eur_kwh") or 0.27
        self.feedin_price_eur_kwh: float = config.get("feedin_price_eur_kwh") or 0.065
        self.residual_power_w: float = config.get("residual_power_w") or 0

        # Geräte-Referenzen (werden von main.py gesetzt)
        self.grid_meter: Meter | None = None
        self.pv_meters: list[Any] = []        # Meter oder VenusOS mit pv_power_mppt()
        self.battery: Battery | None = None
        self.consumption_meter: Meter | None = None

        # Loadpoints
        from core.loadpoint import Loadpoint
        self.loadpoints: list[Loadpoint] = []

        # Circuit Protection
        self.circuits = CircuitManager()

        # Vehicle Manager
        from drivers.vehicle.cloud_api import VehicleManager
        self.vehicles = VehicleManager()

        # Forecast + Tariff (werden von main.py gesetzt)
        self.solar_forecast = None  # SolarForecast instance
        self.tariff = None  # AWATTarTariff instance
        self.feedin_tariff = None  # OemagFeedinTariff (v1.11)
        self.grid_peak = None  # GridPeakTracker (v1.11)

        # Letzte berechnete Werte
        self.grid_power_w: float = 0
        self.pv_power_w: float = 0
        self.battery_power_w: float = 0
        self.battery_soc: float = 0
        self.consumption_w: float = 0
        self.available_w: float = 0
        self.pv_surplus_w: float = 0
        self.pv_surplus_w_for_ev: float = 0  # nach ev_priority_fraction (v1.10)

    def update(self) -> dict:
        """Hauptregelzyklus — alle 30 Sekunden aufrufen.

        Returns:
            dict mit aktuellem Site-State (für MQTT site_state Topic)
        """
        # 1. Messwerte lesen — GenericModbus hat alle Werte in _last_metrics
        if self.grid_meter and hasattr(self.grid_meter, '_last_metrics'):
            m = self.grid_meter._last_metrics

            # Grid — universell: aggregiert ODER Einzelphasen
            if "grid_power_total" in m:
                self.grid_power_w = m["grid_power_total"] or 0
            else:
                self.grid_power_w = (
                    (m.get("grid_power", 0) or 0) +
                    (m.get("grid_power_l2", 0) or 0) +
                    (m.get("grid_power_l3", 0) or 0)
                )

            # PV — universell: aggregiert ODER DC+AC Einzelwerte
            if "pv_power" in m:
                self.pv_power_w = m["pv_power"] or 0
            else:
                dc_pv = m.get("pv_dc_total", 0) or 0
                if dc_pv == 0:
                    dc_pv = (m.get("pv_mppt_1", 0) or 0) + (m.get("pv_mppt_2", 0) or 0)
                ac_pv = (
                    (m.get("pv_acout_l1", 0) or 0) +
                    (m.get("pv_acout_l2", 0) or 0) +
                    (m.get("pv_acout_l3", 0) or 0) +
                    (m.get("pv_acin_l1", 0) or 0) +
                    (m.get("pv_acin_l2", 0) or 0) +
                    (m.get("pv_acin_l3", 0) or 0)
                )
                self.pv_power_w = dc_pv + ac_pv

            # Battery
            self.battery_power_w = m.get("battery_power", 0) or 0
            self.battery_soc = m.get("battery_soc", 0) or 0

            # Consumption — universell: aggregiert ODER Einzelphasen
            if "consumption" in m:
                self.consumption_w = m["consumption"] or 0
            else:
                self.consumption_w = (
                    (m.get("ac_consumption_l1", 0) or 0) +
                    (m.get("ac_consumption_l2", 0) or 0) +
                    (m.get("ac_consumption_l3", 0) or 0)
                )

            log.debug("Site Metrics: grid=%.0f pv=%.0f bat=%.0f soc=%.0f cons=%.0f keys=%s",
                       self.grid_power_w, self.pv_power_w, self.battery_power_w,
                       self.battery_soc, self.consumption_w, list(m.keys()))
        else:
            # Fallback: ABC Interface (wenn kein _last_metrics vorhanden)
            self.grid_power_w = self.grid_meter.current_power() if self.grid_meter else 0
            self.pv_power_w = self._read_pv_power()
            self._read_battery()
            # Consumption: bevorzugt consumption_power() (z.B. VenusOS),
            # sonst current_power() (generisches Meter)
            if self.consumption_meter:
                if hasattr(self.consumption_meter, 'consumption_power'):
                    self.consumption_w = self.consumption_meter.consumption_power()
                elif self.consumption_meter is not self.grid_meter:
                    self.consumption_w = self.consumption_meter.current_power()
                else:
                    self.consumption_w = 0  # Gleicher Meter wie Grid → aus _last_metrics

        # Netzspitze im Viertelstundenraster mitschreiben (v1.11)
        if self.grid_peak:
            self.grid_peak.update(self.grid_power_w)

        # 2. Verfügbare Leistung berechnen (wie evcc)
        #
        # Formel: available = aktuelle_LP_Leistung + (-grid) - buffer
        #
        # Das bedeutet: "Wie viel können alle Loadpoints zusammen verbrauchen,
        # ohne dass Strom aus dem Netz bezogen wird?"
        #
        # Beispiel: LP lädt mit 10kW, Grid importiert 45W, Buffer 100W
        #   → available = 10000 - 45 - 100 = 9855W → LP reduziert auf 9.8kW
        #
        # Beispiel: LP aus, Grid exportiert 5kW (Einspeisung), Buffer 100W
        #   → available = 0 + 5000 - 100 = 4900W → LP kann mit 4.9kW starten
        #
        # LP-Leistung: wenn Auto beauftragt ist (enabled + target > 0), nutze
        # Soll-Leistung statt gemessene. Das stabilisiert die Berechnung
        # waehrend des Ramp-Ups und verhindert Oszillation (measured_power
        # hinkt grid_power hinterher, weil LP.update() spaeter im Zyklus laeuft).
        def _lp_power_for_calc(lp):
            if lp._last_written_enabled and lp._target_current_a > 0:
                return lp._target_current_a * getattr(lp, "_w_per_a", 230 * lp.phases)
            return lp._charging_power_w

        current_lp_power = sum(_lp_power_for_calc(lp) for lp in self.loadpoints)
        surplus_w = current_lp_power - self.grid_power_w - self.buffer_w

        # Auto-vor-Batterie (evcc-Style), jetzt mit ev_priority_fraction (v1.10):
        # Wenn Hausbatterie gerade laedt UND SoC ueber priority_soc, steht ein
        # Anteil dieser Ladeleistung dem Auto zur Verfuegung (PV-Ueberschuss
        # geht erst ins Auto, dann in die Batterie). Bei fraction=1.0 (Default,
        # bisheriges Verhalten): 100% umleitbar. Bei kleinerem Wert: weniger
        # aggressiv, Batterie behaelt mehr.
        #
        # WICHTIG (Verhalten von battery_redirect_w bei available_w/pv-Modus):
        # Die Formel surplus_w = current_lp_power - grid - buffer verwendet
        # die AKTUELLE LP-Leistung als Basis und regelt ueber die Grid-
        # Abweichung nach — das ist ein Regelkreis, kein direkter Blockwert.
        # Dadurch konvergiert battery_redirect_w NICHT zu einem sauberen
        # Prozentsatz-Split (das Auto "erobert" iterativ immer mehr vom
        # PV-Ueberschuss, bis die Batterie nur noch den Buffer-Rest bekommt —
        # unabhaengig vom fraction-Wert, nur langsamer bei kleinerem fraction).
        # Fuer eine SAUBERE Prozentsatz-Aufteilung wird stattdessen
        # pv_surplus_w_for_ev (unten) verwendet, das direkt aus PV-Leistung
        # und Hausgrundlast berechnet wird — ohne Regelkreis-Rueckkopplung.
        # battery_redirect_w bleibt fuer den "pv"-Modus (on/off-Modus mit
        # Hysterese) als richtungsweisende, aber nicht exakt proportionale
        # Drosselung bestehen.
        battery_redirect_w = 0
        if self.battery_power_w > 50:  # Batterie laedt
            if self.priority_soc <= 0 or self.battery_soc >= self.priority_soc:
                battery_redirect_w = self.battery_power_w * self.ev_priority_fraction
                log.debug("Auto-Vorrang: Batterie laedt %.0fW -> %.0fW umleitbar (Anteil %.0f%%, SoC %.0f%%)",
                          self.battery_power_w, battery_redirect_w,
                          self.ev_priority_fraction * 100, self.battery_soc)

        # Grid-Limit als Obergrenze (schützt vor Überlast am Netzanschluss)
        grid_headroom_w = self.grid_limit_w - self.grid_power_w - self.buffer_w

        self.available_w = min(surplus_w + battery_redirect_w, grid_headroom_w)

        # Echter PV-Surplus (ohne Batterie als Quelle):
        # PV-Leistung minus Hausgrundlast (= consumption_w ohne LPs).
        # Wird im min_pv-Modus benutzt, damit der LP NICHT die Batterie
        # leerzieht, wenn nicht genug PV da ist (User-Bug-Report 01.05.2026).
        # available_w (oben) bleibt unveraendert — pv-only-Mode mit Hysterese
        # nutzt diese Formel weiterhin.
        #
        # house_base_w wird aus dem UNABHAENGIG gemessenen consumption_w
        # berechnet (Meter-Wert minus aktuelle LP-Leistung) — keine
        # Rueckkopplungsschleife wie bei surplus_w oben. self.pv_surplus_w
        # ist deshalb der TATSAECHLICHE Gesamt-Pool fuer Auto+Batterie
        # zusammen, gemessen frisch in jedem Zyklus.
        # GEMESSENE LP-Leistung abziehen, nicht die nominelle Soll-Leistung aus
        # _lp_power_for_calc: consumption_w enthaelt den realen Bezug der Wallbox.
        # Die Zoe zieht bei 9 A real ~4,65 kW statt nominell 6,2 kW; mit dem
        # Nominalwert wurde die Grundlast um ~1,5 kW zu niedrig und der
        # PV-Ueberschuss entsprechend zu hoch gerechnet.
        measured_lp_power = sum(lp._charging_power_w for lp in self.loadpoints)
        house_base_w = max(0, self.consumption_w - measured_lp_power)
        self.pv_surplus_w = max(0, self.pv_power_w - house_base_w - self.buffer_w)

        # PV-Priorisierung Auto vs. Speicher (v1.10): dieser Teil des
        # pv_surplus_w-Pools wird dem Auto angeboten (min_pv-Modus). Der Rest
        # (self.pv_surplus_w - pv_surplus_w_for_ev) fliesst automatisch in
        # die Batterie — nicht durch dieses Skript gesteuert, sondern durch
        # Victrons eigene ESS-Logik (zero-grid-import), die jede vom Auto
        # NICHT abgerufene PV-Leistung selbststaendig in die Batterie
        # umleitet. Deshalb genuegt es, dem Auto nur seinen Anteil
        # anzubieten — kein explizites Batterie-Limit noetig.
        self.pv_surplus_w_for_ev = self.pv_surplus_w * self.ev_priority_fraction

        # Ungenutzten Batterie-Anteil ans Auto umleiten (v1.10.3):
        # Wenn die Batterie ihren reservierten Anteil nicht abruft (z.B. voll,
        # Temperatur-Drosselung, Absorption-Tapering am Ladeende), wuerde die
        # Differenz sonst ungenutzt ins Netz gehen statt dem Auto zuzufliessen.
        # Bug-Report 18.07.2026: Speicher 100% voll, Slider auf 50/50, nur die
        # Haelfte des Ueberschusses ging ans Auto, der Rest wurde exportiert
        # statt dem Auto angeboten zu werden.
        battery_share_w = self.pv_surplus_w - self.pv_surplus_w_for_ev
        battery_unused_w = max(0, battery_share_w - max(0, self.battery_power_w))
        if battery_unused_w > 0:
            self.pv_surplus_w_for_ev = min(self.pv_surplus_w, self.pv_surplus_w_for_ev + battery_unused_w)
            log.debug("Batterie nutzt reservierten Anteil nicht (Soll %.0fW, Ist %.0fW, SoC %.0f%%) -> %.0fW zusaetzlich ans Auto",
                      battery_share_w, self.battery_power_w, self.battery_soc, battery_unused_w)

        # Batterie-Vorrang unter priority_soc:
        # Unter prioritySoc → Batterie hat Vorrang, nichts fuer Loadpoints
        if self.priority_soc > 0 and self.battery_soc < self.priority_soc:
            self.available_w = min(self.available_w, 0)
            log.debug("Battery priority: SoC %.0f%% < %.0f%% — Loadpoints gedrosselt",
                      self.battery_soc, self.priority_soc)

        log.debug("Available: surplus=%.0fW bat_redirect=%.0fW grid_headroom=%.0fW lp_power=%.0fW → available=%.0fW",
                  surplus_w, battery_redirect_w, grid_headroom_w, current_lp_power, self.available_w)

        # 3. Circuit-Lasten zurücksetzen
        self.circuits.reset_all()

        # 4. Loadpoints aktualisieren (nach Priorität)
        remaining_w = self.available_w
        for lp in sorted(self.loadpoints, key=lambda lp: lp.priority):
            # Battery Boost (wie evcc): Hausbatterie darf entladen werden
            # um das Auto schneller zu laden. Priority-SoC wird ignoriert.
            # Konsequenz: available_w wird erhoeht um die komplette Battery-Kapazitaet
            # -> Loadpoint kann bis zu max_current ziehen, Batterie deckt Defizit.
            lp_boost_w = 0
            if getattr(lp, "battery_boost", False):
                # Volle Boost-Leistung: erlaubt LP bis max_current
                # Hausbatterie entlaedt automatisch wenn PV nicht reicht
                lp_boost_w = lp.max_current * 230 * lp.phases
                log.debug("Battery Boost LP %s: aktiv (SoC %.0f%%, bat=%.0fW)",
                          lp.name, self.battery_soc, self.battery_power_w)

            # Circuit-Limit prüfen (falls konfiguriert)
            circuit_id = getattr(lp, "circuit_id", None)
            circuit_max_a = self.circuits.available_for_loadpoint(circuit_id)
            circuit_max_w = circuit_max_a * 230 * lp.phases

            # Loadpoint bekommt das Minimum aus verfügbar + Boost + Circuit-Limit
            lp_available_w = min(remaining_w + lp_boost_w, circuit_max_w)
            # PV-Surplus auch begrenzen auf circuit_max_w + boost.
            # pv_surplus_w_for_ev (nicht pv_surplus_w!) beruecksichtigt die
            # ev_priority_fraction — der Rest bleibt automatisch fuer die
            # Batterie (siehe Kommentar oben bei der Berechnung).
            lp_pv_surplus_w = min(self.pv_surplus_w_for_ev + lp_boost_w, circuit_max_w)
            used_w = lp.update(lp_available_w, self.grid_power_w, pv_surplus_w=lp_pv_surplus_w)
            remaining_w -= used_w

            # Circuit-Last aktualisieren
            if circuit_id:
                circuit = self.circuits.get_circuit(circuit_id)
                if circuit:
                    circuit.add_load(used_w / (230 * lp.phases))

        log.info(
            "Site: Grid=%.0fW PV=%.0fW Bat=%.0fW(%.0f%%) Consumption=%.0fW Available=%.0fW",
            self.grid_power_w, self.pv_power_w, self.battery_power_w,
            self.battery_soc, self.consumption_w, self.available_w,
        )

        return self._build_state()

    def _read_pv_power(self) -> float:
        """Liest PV-Leistung von allen PV-Quellen."""
        total = 0.0
        for pv in self.pv_meters:
            if hasattr(pv, "pv_power"):
                # VenusOS: pv_power() liest System-Register 850 + MPPT
                total += pv.pv_power()
            elif hasattr(pv, "pv_power_mppt"):
                total += pv.pv_power_mppt()
            elif isinstance(pv, Meter):
                total += abs(pv.current_power())
        return total

    def _read_battery(self):
        if self.battery:
            self.battery_soc = self.battery.soc()
            # battery_power() bevorzugen (gibt echte Battery-Werte),
            # current_power() wuerde bei VenusOS Grid-Werte liefern!
            if hasattr(self.battery, 'battery_power'):
                self.battery_power_w = self.battery.battery_power()
            else:
                self.battery_power_w = self.battery.current_power()
        else:
            self.battery_soc = 0
            self.battery_power_w = 0

    def _build_state(self) -> dict:
        """Baut das site_state Dict für MQTT."""
        state = {
            "grid_w": round(self.grid_power_w),
            "pv_w": round(self.pv_power_w),
            "battery_w": round(self.battery_power_w),
            "battery_soc": round(self.battery_soc, 1),
            "consumption_w": round(self.consumption_w),
            "available_w": round(self.available_w),
            "pv_surplus_w": round(self.pv_surplus_w),
            "pv_surplus_w_for_ev": round(self.pv_surplus_w_for_ev),
            "ev_priority_pct": round(self.ev_priority_fraction * 100),
            "loadpoints": [lp.state() for lp in self.loadpoints],
        }

        # Battery-Details (Voltage/Current) wenn Driver sie bereitstellt
        if self.battery:
            try:
                if hasattr(self.battery, "battery_voltage"):
                    bv = self.battery.battery_voltage()
                    if bv is not None:
                        state["battery_voltage"] = round(bv, 2)
                if hasattr(self.battery, "battery_current"):
                    bc = self.battery.battery_current()
                    if bc is not None:
                        state["battery_current"] = round(bc, 2)
            except Exception:
                pass

        # Circuit-Status anhängen (wenn konfiguriert)
        circuit_state = self.circuits.state()
        if circuit_state:
            state["circuits"] = circuit_state

        # Fahrzeug-Status anhängen (wenn vorhanden)
        vehicles = self.vehicles.all_vehicles()
        if vehicles:
            state["vehicles"] = vehicles

        # Tarife
        state["grid_price_ct"] = round(self.grid_price_eur_kwh * 100, 1)
        state["feedin_price_ct"] = round(self.feedin_price_eur_kwh * 100, 1)
        if self.feedin_tariff:
            self.feedin_price_eur_kwh = self.feedin_tariff.effective_ct / 100.0
            state["feedin_price_ct"] = round(self.feedin_tariff.effective_ct, 2)
            state["feedin"] = self.feedin_tariff.to_dict()
        if self.grid_peak:
            state["grid_peak"] = self.grid_peak.state()

        # Forecast
        if self.solar_forecast:
            state["forecast"] = self.solar_forecast.to_dict()

        # Dynamic Tariff
        if self.tariff:
            state["tariff"] = self.tariff.to_dict()

        return state

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

        # Glättung: EWMA (Exponentially Weighted Moving Average, wie evcc)
        self._available_smoothed: float = 0
        self._available_initialized: bool = False
        self._ewma_factor: float = 0.3  # 0.3 = reagiert in ~3 Zyklen (30s)
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

        # Letzte berechnete Werte
        self.grid_power_w: float = 0
        self.pv_power_w: float = 0
        self.battery_power_w: float = 0
        self.battery_soc: float = 0
        self.consumption_w: float = 0
        self.available_w: float = 0

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
            # Fallback: ABC Interface
            self.grid_power_w = self.grid_meter.current_power() if self.grid_meter else 0
            self.pv_power_w = self._read_pv_power()
            self._read_battery()
            self.consumption_w = (
                self.consumption_meter.current_power() if self.consumption_meter else 0
            )

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
        current_lp_power = sum(lp._charging_power_w for lp in self.loadpoints)
        surplus_w = current_lp_power - self.grid_power_w - self.buffer_w

        # Grid-Limit als Obergrenze (schützt vor Überlast am Netzanschluss)
        grid_headroom_w = self.grid_limit_w - self.grid_power_w - self.buffer_w

        raw_available_w = min(surplus_w, grid_headroom_w)

        # EWMA-Glättung (wie evcc): reagiert schnell auf Trends, dämpft Spitzen
        if not self._available_initialized:
            self._available_smoothed = raw_available_w
            self._available_initialized = True
        else:
            self._available_smoothed = (
                self._ewma_factor * raw_available_w +
                (1 - self._ewma_factor) * self._available_smoothed
            )
        self.available_w = self._available_smoothed

        # Battery Priority (wie evcc prioritySoc/bufferSoc):
        # Unter prioritySoc → Batterie hat Vorrang, weniger für Loadpoints
        if self.priority_soc > 0 and self.battery_soc < self.priority_soc:
            self.available_w = min(self.available_w, 0)
            log.debug("Battery priority: SoC %.0f%% < %.0f%% — Loadpoints gedrosselt",
                      self.battery_soc, self.priority_soc)

        log.debug("Available: surplus=%.0fW grid_headroom=%.0fW lp_power=%.0fW → available=%.0fW",
                  surplus_w, grid_headroom_w, current_lp_power, self.available_w)

        # 3. Circuit-Lasten zurücksetzen
        self.circuits.reset_all()

        # 4. Loadpoints aktualisieren (nach Priorität)
        remaining_w = self.available_w
        for lp in sorted(self.loadpoints, key=lambda lp: lp.priority):
            # Battery Boost: Batterie-Entladeleistung zum verfügbaren Strom hinzurechnen
            lp_boost_w = 0
            if getattr(lp, "battery_boost", False) and self.battery_power_w < -50:
                # Batterie entlädt → diese Leistung steht dem LP zusätzlich zur Verfügung
                # Aber nur wenn SoC über priority_soc (Batterie nicht zu leer)
                if self.priority_soc <= 0 or self.battery_soc > self.priority_soc:
                    lp_boost_w = abs(self.battery_power_w)
                    log.debug("Battery Boost LP %s: +%.0fW (SoC %.0f%%)",
                              lp.name, lp_boost_w, self.battery_soc)

            # Circuit-Limit prüfen (falls konfiguriert)
            circuit_id = getattr(lp, "circuit_id", None)
            circuit_max_a = self.circuits.available_for_loadpoint(circuit_id)
            circuit_max_w = circuit_max_a * 230 * lp.phases

            # Loadpoint bekommt das Minimum aus verfügbar + Boost + Circuit-Limit
            lp_available_w = min(remaining_w + lp_boost_w, circuit_max_w)
            used_w = lp.update(lp_available_w)
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
            if hasattr(pv, "pv_power_mppt"):
                total += pv.pv_power_mppt()
            elif isinstance(pv, Meter):
                total += abs(pv.current_power())
        return total

    def _read_battery(self):
        if self.battery:
            self.battery_soc = self.battery.soc()
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
            "loadpoints": [lp.state() for lp in self.loadpoints],
        }

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

        # Forecast
        if self.solar_forecast:
            state["forecast"] = self.solar_forecast.to_dict()

        # Dynamic Tariff
        if self.tariff:
            state["tariff"] = self.tariff.to_dict()

        return state

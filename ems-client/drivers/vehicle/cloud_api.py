"""Vehicle API — Fahrzeug-SoC über Hersteller-Cloud-APIs.

Holt den Batterie-Ladezustand (SoC) und die Reichweite von
Elektrofahrzeugen direkt vom Pi über die Cloud-APIs der Hersteller.

Unterstützte Hersteller:
  - Renault / Dacia (Kamereon API via renault-api)

Konfigurationsbeispiel (in wald-ems.yaml):
vehicles:
  - name: "Renault Zoe"
    manufacturer: renault
    vin: "VF1..."
    battery_kwh: 52
    credentials:
      email: "user@example.com"
      password: "secret"
"""

import logging
import time
from drivers import register

log = logging.getLogger("ems.vehicle")


class Vehicle:
    """Fahrzeug-Datenobjekt — wird vom Pi aus der DB geladen."""

    def __init__(self, config: dict):
        self.id = config.get("id", "")
        self.name = config.get("name", "Fahrzeug")
        self.manufacturer = config.get("manufacturer", "")
        self.vin = config.get("vin", "")
        self._soc: float = config.get("soc", 0)
        self._range_km: float = config.get("range_km", 0)
        self._last_updated: str = config.get("last_updated", "")
        self.min_soc: float = config.get("min_soc", 20)
        self.target_soc: float = config.get("target_soc", 80)
        self.loadpoint_id: str = config.get("loadpoint_id", "")

    @property
    def soc(self) -> float:
        return self._soc

    @soc.setter
    def soc(self, value: float):
        self._soc = value

    @property
    def range_km(self) -> float:
        return self._range_km

    def update_from_db(self, data: dict):
        """Aktualisiert Fahrzeugdaten aus DB-Antwort."""
        if "soc" in data:
            self._soc = data["soc"]
        if "range_km" in data:
            self._range_km = data["range_km"]
        if "last_updated" in data:
            self._last_updated = data["last_updated"]

    def should_charge(self) -> bool:
        """Ob das Fahrzeug geladen werden soll (unter target_soc)."""
        return self._soc < self.target_soc

    def needs_charge(self) -> bool:
        """Ob dringend geladen werden muss (unter min_soc)."""
        return self._soc < self.min_soc

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "vin": self.vin,
            "soc": self._soc,
            "range_km": self._range_km,
            "min_soc": self.min_soc,
            "target_soc": self.target_soc,
            "should_charge": self.should_charge(),
        }


class VehicleManager:
    """Verwaltet alle Fahrzeuge eines Standorts."""

    def __init__(self):
        self.vehicles: dict[str, Vehicle] = {}

    def load_from_config(self, vehicles_config: list[dict]):
        """Lädt Fahrzeuge aus der Site-Konfiguration."""
        self.vehicles.clear()
        for vc in vehicles_config:
            v = Vehicle(vc)
            self.vehicles[v.id] = v
            log.info("Fahrzeug geladen: %s (%s, SoC: %.0f%%)",
                     v.name, v.manufacturer, v.soc)

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        return self.vehicles.get(vehicle_id)

    def get_vehicle_for_loadpoint(self, loadpoint_id: str) -> Vehicle | None:
        """Findet das Fahrzeug, das einem Ladepunkt zugeordnet ist."""
        for v in self.vehicles.values():
            if v.loadpoint_id == loadpoint_id:
                return v
        return None

    def update_soc(self, vehicle_id: str, soc: float, range_km: float = 0):
        """Aktualisiert SoC eines Fahrzeugs (z.B. aus MQTT-Push)."""
        v = self.vehicles.get(vehicle_id)
        if v:
            v.soc = soc
            if range_km:
                v._range_km = range_km
            log.info("Fahrzeug %s SoC → %.0f%%", v.name, soc)

    def all_vehicles(self) -> list[dict]:
        return [v.to_dict() for v in self.vehicles.values()]

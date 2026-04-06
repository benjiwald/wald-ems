"""Vehicle Cloud API Bridge — Fahrzeug-SoC über Hersteller-Cloud-APIs.

Holt den Batterie-Ladezustand (SoC) und die Reichweite von
Elektrofahrzeugen über die Cloud-APIs der Hersteller.

Unterstützte Hersteller:
  - Tesla (Fleet API, OAuth2)
  - Volkswagen / Audi / Skoda / SEAT / CUPRA (WeConnect)
  - BMW / MINI (ConnectedDrive)
  - Mercedes / Smart (Mercedes me)
  - Porsche (My Porsche / E-Mobility API)
  - Renault / Dacia (Kamereon API)
  - Hyundai / Kia (Bluelink)
  - Peugeot / Citroën / Opel / DS (Stellantis)

Architektur:
  Der Vehicle-Treiber läuft NICHT auf dem Pi, sondern als
  Supabase Edge Function, da Cloud-APIs OAuth2 + Redirect-URLs
  benötigen, die auf dem Pi nicht praktikabel sind.

  Pi → MQTT "vehicle_soc_request" → Edge Function → Cloud API → DB
  Dashboard → DB → SoC anzeigen

  Alternativ: Pi pollt SoC aus der DB (site_config.vehicles[].soc).

Konfigurationsbeispiel (in site_config.vehicles[]):
{
    "id": "vehicle-1",
    "name": "Tesla Model 3",
    "manufacturer": "tesla",
    "vin": "5YJ3E1EA1NF123456",
    "soc": 72,
    "range_km": 285,
    "last_updated": "2026-03-25T14:30:00Z",
    "min_soc": 20,
    "target_soc": 80,
    "loadpoint_id": "lp-uuid"
}
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
    """Verwaltet alle Fahrzeuge eines Standorts.

    Fahrzeug-SoC wird über die DB synchronisiert:
    1. Edge Function pollt Cloud APIs alle 5 Min
    2. Schreibt SoC in vehicles-Tabelle (oder site_config)
    3. Pi liest SoC beim Config-Refresh (alle 5 Min)
    """

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


# ── Edge Function Template für Vehicle Cloud API ─────────────────────────────
#
# Die tatsächliche Cloud-API-Kommunikation läuft als Supabase Edge Function,
# nicht auf dem Pi. Hier das Konzept:
#
# Edge Function: vehicle-soc (alle 5 Min via pg_cron)
#
# 1. Liest alle vehicles aus der DB mit OAuth2-Tokens
# 2. Pro Hersteller:
#    - Tesla: Fleet API → GET /api/1/vehicles/{id}/vehicle_data
#    - VW/Audi: WeConnect API → GET /vehicles/{vin}/status
#    - BMW: ConnectedDrive → GET /vehicles/v1/{vin}/status
#    - Mercedes: Mercedes me → GET /vehicles/{vin}/status
#    - Renault: Kamereon → GET /accounts/{id}/vehicles/{vin}/status
# 3. Extrahiert SoC + Range
# 4. Schreibt in vehicles-Tabelle
# 5. Pi liest beim nächsten Config-Refresh
#
# OAuth2-Tokens werden über das Dashboard eingegeben und in der DB gespeichert.
# Token-Refresh passiert automatisch in der Edge Function.

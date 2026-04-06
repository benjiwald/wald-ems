"""Config Manager — YAML-basiert für lokalen Betrieb (ersetzt Supabase RPC)."""

import logging
import os
import yaml

log = logging.getLogger("ems.config")


class ConfigManager:
    """Verwaltet Assets, Loadpoints und Site-Config aus YAML-Datei.

    Ersetzt die Supabase-basierte Version komplett.
    Liest wald-ems.yaml und konvertiert in das Format, das Drivers/Site erwarten.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self._mtime = 0.0

        self.assets: list[dict] = []
        self.loadpoints: list[dict] = []
        self.vehicles: list[dict] = []
        self.site_config: dict = {}
        self.control_interval: int = 10
        self.telemetry_interval: int = 30
        self.db_path: str = ""
        self.retention_days: int = 30

    def load_initial(self):
        """Lädt Config beim Start aus YAML."""
        self._load_yaml()

    def refresh_if_needed(self, interval: float = 300) -> bool:
        """Prüft ob sich die YAML-Datei geändert hat. Returns True bei Änderung."""
        try:
            mtime = os.path.getmtime(self.config_path)
            if mtime != self._mtime:
                old_assets = str(self.assets)
                self._load_yaml()
                return str(self.assets) != old_assets
        except Exception as e:
            log.debug("Config-Refresh fehlgeschlagen: %s", e)
        return False

    def ping_supabase(self):
        """Nicht nötig lokal — Stub für Kompatibilität."""
        pass

    def update_loadpoint_mode(self, lp_name: str, mode: str):
        """Aktualisiert Mode im Loadpoint (nur im Speicher, nicht in YAML)."""
        for lp in self.loadpoints:
            if lp.get("name") == lp_name:
                lp["mode"] = mode
                log.info("Loadpoint %s → mode=%s", lp_name, mode)

    def update_vehicle_soc(self, vehicle_id: str, soc: float,
                           range_km: float = 0, is_charging: bool = False):
        """Stub — SoC wird nur im Speicher gehalten."""
        pass

    def update_loadpoint_field(self, lp_name: str, field: str, value):
        """Aktualisiert ein Loadpoint-Feld im Speicher."""
        for lp in self.loadpoints:
            if lp.get("name") == lp_name:
                lp[field] = value

    def handle_config_push(self, config: dict):
        """Stub — kein MQTT-Push im lokalen Modus."""
        pass

    # ── Private ──────────────────────────────────────────────────────────────

    def _load_yaml(self):
        """Liest und parsed wald-ems.yaml."""
        try:
            with open(self.config_path, "r") as f:
                raw = yaml.safe_load(f) or {}
            self._mtime = os.path.getmtime(self.config_path)
        except FileNotFoundError:
            log.error("Config-Datei nicht gefunden: %s", self.config_path)
            return
        except Exception as e:
            log.error("Config-Fehler: %s", e)
            return

        # Site Config
        site = raw.get("site", {})
        self.site_config = {
            "name": site.get("name", "Wald EMS"),
            "grid_limit_kw": site.get("grid_limit_kw", 11),
            "buffer_w": site.get("buffer_w", 100),
            "priority_soc": site.get("priority_soc", 0),
            "grid_price_eur_kwh": site.get("grid_price_eur_kwh", 0.27),
            "feedin_price_eur_kwh": site.get("feedin_price_eur_kwh", 0.065),
        }

        # Forecast in site_config einbauen (für build_site Kompatibilität)
        forecast = raw.get("forecast")
        if forecast:
            self.site_config["forecast"] = forecast

        # Tariff in site_config einbauen
        tariff = raw.get("tariff")
        if tariff:
            self.site_config["tariff"] = tariff

        # Database
        db_cfg = raw.get("database", {})
        self.db_path = db_cfg.get("path", "./wald-ems.db")
        self.retention_days = db_cfg.get("retention_days", 30)

        # Meters → Assets (Konvertierung ins Driver-Format)
        self.assets = []
        for i, meter in enumerate(raw.get("meters", [])):
            asset = {
                "id": f"meter_{i}",
                "name": meter.get("name", f"Meter {i}"),
                "asset_type": "meter",
                "driver_type": meter.get("type", "generic_modbus"),
                "modbus_host": meter.get("host", ""),
                "modbus_port": meter.get("port", 502),
                "modbus_unit_id": meter.get("unit_id", 1),
            }
            if "register_map" in meter:
                asset["modbus_register_map"] = meter["register_map"]
            self.assets.append(asset)

        # Chargers → Assets
        for i, charger in enumerate(raw.get("chargers", [])):
            asset = {
                "id": f"charger_{i}",
                "name": charger.get("name", f"Charger {i}"),
                "asset_type": "charger",
                "driver_type": charger.get("type", "generic_modbus"),
                "modbus_host": charger.get("host", ""),
                "modbus_port": charger.get("port", 502),
                "modbus_unit_id": charger.get("unit_id", 1),
            }
            if "register_map" in charger:
                asset["modbus_register_map"] = charger["register_map"]
            self.assets.append(asset)

        # Loadpoints (referenzieren Chargers/Meters per Name)
        self.loadpoints = []
        charger_name_to_id = {c.get("name"): f"charger_{i}" for i, c in enumerate(raw.get("chargers", []))}
        meter_name_to_id = {m.get("name"): f"meter_{i}" for i, m in enumerate(raw.get("meters", []))}

        for lp in raw.get("loadpoints", []):
            charger_name = lp.get("charger", "")
            meter_name = lp.get("meter", "")
            self.loadpoints.append({
                "id": lp.get("name", ""),  # Name als ID
                "name": lp.get("name", ""),
                "charger_asset_id": charger_name_to_id.get(charger_name, ""),
                "meter_asset_id": meter_name_to_id.get(meter_name, "") or charger_name_to_id.get(meter_name, ""),
                "mode": lp.get("mode", "off"),
                "min_current": lp.get("min_current", 6),
                "max_current": lp.get("max_current", 16),
                "phases": lp.get("phases", 1),
                "min_soc": lp.get("min_soc", 0),
                "target_soc": lp.get("target_soc", 100),
            })

        # Vehicles
        self.vehicles = []
        for i, v in enumerate(raw.get("vehicles", [])):
            self.vehicles.append({
                "id": f"vehicle_{i}",
                "name": v.get("name", f"Vehicle {i}"),
                "manufacturer": v.get("manufacturer", ""),
                "vin": v.get("vin", ""),
                "locale": v.get("locale", "de_AT"),
                "credentials": v.get("credentials", {}),
                "loadpoint_id": v.get("loadpoint", ""),
                "battery_kwh": v.get("battery_kwh", 50),
            })

        log.info("Config geladen: %d Assets, %d Loadpoints, %d Vehicles",
                 len(self.assets), len(self.loadpoints), len(self.vehicles))

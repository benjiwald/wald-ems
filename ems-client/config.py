"""Config Manager — YAML-basiert für lokalen Betrieb (ersetzt Supabase RPC)."""

import logging
import os
import yaml

log = logging.getLogger("ems.config")

# ── Default Register-Maps für bekannte Gerätetypen ─────────────────────────
# In der Cloud-Version (Wania EMS) kommen diese aus der Datenbank.
# Hier werden sie als Defaults eingebaut, damit die YAML einfach bleibt.

DEFAULT_REGISTER_MAPS = {
    "victron_venus_system": {
        "grid_power_l1":     {"address": 820, "type": "int16",  "scale": 1, "unit": "W", "metric_key": "grid_w"},
        "grid_power_l2":     {"address": 821, "type": "int16",  "scale": 1, "unit": "W"},
        "grid_power_l3":     {"address": 822, "type": "int16",  "scale": 1, "unit": "W"},
        "battery_power":     {"address": 842, "type": "int16",  "scale": 1, "unit": "W", "metric_key": "battery_w"},
        "battery_soc":       {"address": 843, "type": "uint16", "scale": 1, "unit": "%", "metric_key": "battery_soc"},
        "battery_voltage":   {"address": 840, "type": "uint16", "scale": 0.1, "unit": "V"},
        "battery_current":   {"address": 841, "type": "int16",  "scale": 0.1, "unit": "A"},
        "ac_consumption_l1": {"address": 817, "type": "uint16", "scale": 1, "unit": "W", "metric_key": "consumption_w"},
        "ac_consumption_l2": {"address": 818, "type": "uint16", "scale": 1, "unit": "W"},
        "ac_consumption_l3": {"address": 819, "type": "uint16", "scale": 1, "unit": "W"},
        # PV: DC (MPPT) + AC (PV-Wechselrichter auf AC-Out und AC-In)
        "pv_dc_power":       {"address": 850, "type": "uint16", "scale": 1, "unit": "W", "metric_key": "pv_dc_total"},
        "pv_acout_l1":       {"address": 808, "type": "uint16", "scale": 1, "unit": "W"},
        "pv_acout_l2":       {"address": 809, "type": "uint16", "scale": 1, "unit": "W"},
        "pv_acout_l3":       {"address": 810, "type": "uint16", "scale": 1, "unit": "W"},
        "pv_acin_l1":        {"address": 811, "type": "uint16", "scale": 1, "unit": "W"},
        "pv_acin_l2":        {"address": 812, "type": "uint16", "scale": 1, "unit": "W"},
        "pv_acin_l3":        {"address": 813, "type": "uint16", "scale": 1, "unit": "W"},
    },
    "sma_sunnyboy": {
        "dc_power":      {"address": 30773, "type": "int32", "scale": 1, "unit": "W", "metric_key": "pv_w"},
        "ac_power":      {"address": 30775, "type": "int32", "scale": 1, "unit": "W"},
        "total_yield":   {"address": 30529, "type": "uint32", "scale": 1, "unit": "Wh"},
        "daily_yield":   {"address": 30535, "type": "uint32", "scale": 1, "unit": "Wh"},
    },
    "fronius_symo": {
        "ac_power":      {"address": 40092, "type": "float32", "scale": 1, "unit": "W", "metric_key": "pv_w"},
        "ac_frequency":  {"address": 40094, "type": "float32", "scale": 1, "unit": "Hz"},
        "total_yield":   {"address": 40096, "type": "float32", "scale": 1, "unit": "Wh"},
    },
    # NRG Kick Gen2 Local API — alle Holding Registers, LSW word order
    # Doku: https://nrgkick.com/wp-content/uploads/local_api_docu_simulate-1.html
    "nrgkick_modbus": {
        "charging_power":       {"address": 210, "type": "int32",  "scale": 0.001, "unit": "W", "word_order": "lsw", "metric_key": "charging_power_w"},
        "session_energy":       {"address": 203, "type": "uint32", "scale": 1,     "unit": "Wh", "word_order": "lsw", "metric_key": "session_energy_wh"},
        "total_energy":         {"address": 199, "type": "uint64", "scale": 1,     "unit": "Wh", "word_order": "lsw"},
        "current_l1":           {"address": 220, "type": "uint16", "scale": 0.001, "unit": "A"},
        "current_l2":           {"address": 221, "type": "uint16", "scale": 0.001, "unit": "A"},
        "current_l3":           {"address": 222, "type": "uint16", "scale": 0.001, "unit": "A"},
        "voltage_l1":           {"address": 217, "type": "uint16", "scale": 0.01,  "unit": "V"},
        "voltage_l2":           {"address": 218, "type": "uint16", "scale": 0.01,  "unit": "V"},
        "voltage_l3":           {"address": 219, "type": "uint16", "scale": 0.01,  "unit": "V"},
        "power_l1":             {"address": 224, "type": "int32",  "scale": 0.001, "unit": "W", "word_order": "lsw"},
        "power_l2":             {"address": 226, "type": "int32",  "scale": 0.001, "unit": "W", "word_order": "lsw"},
        "power_l3":             {"address": 228, "type": "int32",  "scale": 0.001, "unit": "W", "word_order": "lsw"},
        "charging_state":       {"address": 251, "type": "uint16", "scale": 1,     "unit": ""},
        "max_current_setpoint": {"address": 194, "type": "uint16", "scale": 0.1,   "unit": "A", "writable": True},
        "charging_pause":       {"address": 195, "type": "uint16", "scale": 1,     "unit": "",  "writable": True},
        "phase_count_max":      {"address": 198, "type": "uint16", "scale": 1,     "unit": "",  "writable": True},
        "max_signaled_current": {"address": 206, "type": "uint16", "scale": 0.1,   "unit": "A"},
    },
}


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
            "demo": site.get("demo", False),
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
                # connection_params: Format das die Drivers erwarten
                "connection_params": {
                    "host": meter.get("host", ""),
                    "port": meter.get("port", 502),
                    "unit_id": meter.get("unit_id", 1),
                    "timeout_ms": meter.get("timeout_ms", 3000),
                },
            }
            # Register-Map: explizit aus YAML, oder Default für bekannten Typ
            driver_type = meter.get("type", "generic_modbus")
            if "register_map" in meter:
                asset["modbus_register_map"] = meter["register_map"]
            elif driver_type in DEFAULT_REGISTER_MAPS:
                asset["modbus_register_map"] = DEFAULT_REGISTER_MAPS[driver_type]
                log.debug("Default Register-Map für %s eingesetzt", driver_type)
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
                "connection_params": {
                    "host": charger.get("host", ""),
                    "port": charger.get("port", 502),
                    "unit_id": charger.get("unit_id", 1),
                    "timeout_ms": charger.get("timeout_ms", 3000),
                },
            }
            # Register-Map: explizit aus YAML, oder Default für bekannten Typ
            charger_driver_type = charger.get("type", "generic_modbus")
            if "register_map" in charger:
                asset["modbus_register_map"] = charger["register_map"]
            elif charger_driver_type in DEFAULT_REGISTER_MAPS:
                asset["modbus_register_map"] = DEFAULT_REGISTER_MAPS[charger_driver_type]
                log.debug("Default Register-Map für Charger %s eingesetzt", charger_driver_type)
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

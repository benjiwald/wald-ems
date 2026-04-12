#!/usr/bin/env python3
"""
Wald EMS Client — Lokales Energiemanagementsystem
==================================================
Lokale Version des Wania EMS Clients.
Kommuniziert via SQLite, Konfiguration via wald-ems.yaml.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

# ── Konfiguration ────────────────────────────────────────────────────────────

# Config-Pfad: Environment > ./wald-ems.yaml > /opt/ems/wald-ems.yaml
CONFIG_PATH = os.environ.get("WALD_EMS_CONFIG", "")
if not CONFIG_PATH:
    for p in ["./wald-ems.yaml", "/opt/ems/wald-ems.yaml"]:
        if os.path.exists(p):
            CONFIG_PATH = p
            break
    if not CONFIG_PATH:
        CONFIG_PATH = "./wald-ems.yaml"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ems")

# ── Imports ──────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_handler import DBHandler, VERSION
from config import ConfigManager
from core.site import Site
from core.loadpoint import Loadpoint
from drivers import load_all_drivers, create_driver
from drivers.modbus.connection import close_all as close_modbus
from api.charger import Charger
from api.meter import Meter

# Optional imports
try:
    from drivers.forecast.solar import SolarForecast
except ImportError:
    SolarForecast = None
try:
    from drivers.tariff.awattar import AWATTarTariff
except ImportError:
    AWATTarTariff = None

# ── Driver Registry laden ────────────────────────────────────────────────────

load_all_drivers()

# ── Globale Instanzen ────────────────────────────────────────────────────────

db: DBHandler | None = None
config: ConfigManager | None = None
site: Site | None = None
drivers: dict[str, object] = {}
_vehicle_drivers: dict[str, object] = {}


def build_drivers(assets: list[dict]) -> dict[str, object]:
    """Erstellt Driver-Instanzen aus Asset-Konfigurationen."""
    result = {}
    for asset in assets:
        asset_id = asset.get("id", "")
        driver_type = asset.get("driver_type", "generic_modbus")
        name = asset.get("name", "unknown")
        try:
            driver = create_driver(driver_type, asset)
            result[asset_id] = driver
            log.info("Driver erstellt: %s → %s (%s)", name, driver_type, asset_id)
        except Exception as e:
            log.error("Driver %s (%s) fehlgeschlagen: %s", name, driver_type, e)
    return result


def build_site(cfg: ConfigManager) -> Site:
    """Erstellt Site mit Geräte-Zuordnungen aus Config."""
    site_config = cfg.site_config or {}
    s = Site(site_config)

    # Solar Forecast
    if SolarForecast and site_config.get("forecast"):
        forecast_cfg = site_config
        s.solar_forecast = SolarForecast(forecast_cfg)
        log.info("Solar Forecast konfiguriert: %.1f kWp (%d Flächen)",
                 s.solar_forecast.kwp, len(s.solar_forecast.planes))

    # Dynamic Tariff
    if AWATTarTariff and site_config.get("tariff"):
        tariff_cfg = site_config
        s.tariff = AWATTarTariff(tariff_cfg)
        log.info("Dynamischer Tarif: aWATTar %s", s.tariff.country)

    # Asset-Type-Map
    asset_types = {}
    for a in cfg.assets:
        asset_types[a["id"]] = a.get("asset_type", "")

    # Grid Meter zuordnen
    grid_id = site_config.get("grid_meter_asset_id")
    if grid_id and grid_id in drivers:
        s.grid_meter = drivers[grid_id]
    else:
        for aid, drv in drivers.items():
            atype = asset_types.get(aid, "")
            if atype in ("inverter", "meter"):
                s.grid_meter = drv
                log.info("Grid Meter: %s (type=%s)", aid, atype)
                break

    if s.grid_meter:
        s.pv_meters = [s.grid_meter]
        s.battery = s.grid_meter
        s.consumption_meter = s.grid_meter

    # Loadpoints erstellen
    for lp_cfg in cfg.loadpoints:
        charger_id = lp_cfg.get("charger_asset_id")
        meter_id = lp_cfg.get("meter_asset_id")
        charger = drivers.get(charger_id)
        meter_drv = drivers.get(meter_id) if meter_id else None
        if charger:
            lp = Loadpoint(lp_cfg, charger, meter_drv)
            s.loadpoints.append(lp)
            log.info("Loadpoint erstellt: %s (charger=%s)", lp.name, charger_id)
        else:
            log.warning("Loadpoint %s: charger %s nicht gefunden", lp_cfg.get("name"), charger_id)

    # Fahrzeuge laden
    _vehicle_drivers.clear()
    for vc in cfg.vehicles:
        manufacturer = vc.get("manufacturer", "").lower()
        lp_id = vc.get("loadpoint_id")
        if manufacturer == "renault":
            try:
                from drivers.vehicle.renault import RenaultVehicle
                rv = RenaultVehicle(vc)
                _vehicle_drivers[vc["id"]] = rv
                if lp_id:
                    for lp in s.loadpoints:
                        if lp.id == lp_id or lp.name == lp_id:
                            lp._vehicle_driver = rv
                            lp._vehicle_battery_kwh = vc.get("battery_kwh", 0)
                            log.info("Fahrzeug %s → LP %s (%.0f kWh)", rv.name, lp.name, lp._vehicle_battery_kwh)
                log.info("Renault-Fahrzeug geladen: %s", rv.name)
            except ImportError:
                log.warning("renault-api nicht installiert — pip install renault-api")
            except Exception as e:
                log.error("Renault-Fahrzeug fehlgeschlagen: %s", e)

    return s


# ── Command Handler ──────────────────────────────────────────────────────────

def handle_command(cmd: dict):
    """Verarbeitet Befehle vom Dashboard (via SQLite commands-Tabelle)."""
    global drivers, site
    action = cmd.get("action", "")
    log.info("Kommando empfangen: %s", action)

    if action == "reload_config":
        try:
            config.load_initial()
            drivers = build_drivers(config.assets)
            site = build_site(config)
            db.publish_log("info", f"Config neu geladen: {len(config.assets)} Geräte, "
                                   f"{len(site.loadpoints)} Ladepunkte")
        except Exception as e:
            log.error("reload_config fehlgeschlagen: %s", e, exc_info=True)
            db.publish_log("error", f"Config laden fehlgeschlagen: {e}")

    elif action == "set_mode":
        lp_name = cmd.get("loadpoint", "")
        mode = cmd.get("mode", "off")
        for lp in (site.loadpoints if site else []):
            if lp.name == lp_name or lp.id == lp_name:
                lp.set_mode(mode)
                config.update_loadpoint_mode(lp_name, mode)
                db.publish_log("info", f"Ladepunkt {lp.name}: Modus → {mode}")
                break

    elif action == "restart_client":
        db.publish_log("info", "Neustart auf Befehl")
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    elif action == "cleanup_db":
        db.cleanup(config.retention_days)

    elif action == "ping":
        db.publish_log("info", "pong", {"ts": datetime.now(timezone.utc).isoformat()})

    else:
        log.warning("Unbekanntes Kommando: %s", action)


# ── Hauptschleife ────────────────────────────────────────────────────────────

def main():
    global db, config, site, drivers

    log.info("Wald EMS Client v%s gestartet", VERSION)
    log.info("Config: %s", CONFIG_PATH)

    # Config laden
    config = ConfigManager(CONFIG_PATH)
    config.load_initial()

    if not config.assets:
        log.warning("Keine Geräte konfiguriert — bitte wald-ems.yaml bearbeiten")

    # DB Handler
    db_path = config.db_path or "./wald-ems.db"
    db = DBHandler(db_path)
    db.on_command(handle_command)
    db.connect()

    # Drivers erstellen
    drivers = build_drivers(config.assets)

    # Site aufbauen
    site = build_site(config)

    db.publish_log("info", f"Wald EMS gestartet: {len(config.assets)} Geräte, "
                           f"{len(site.loadpoints if site else [])} Ladepunkte")

    # ── Demo-Modus ───────────────────────────────────────────────────────────

    demo_mode = (
        os.environ.get("WALD_EMS_DEMO", "").strip() in ("1", "true", "yes")
        or config.site_config.get("demo", False)
        or (not config.assets and not drivers)
    )

    demo_site = None
    if demo_mode:
        from demo import DemoSite
        log.info("═══ DEMO-MODUS aktiv — Simulationsdaten ═══")
        demo_site = DemoSite(config.site_config)
        db.publish_log("info", "Demo-Modus aktiv — keine echte Hardware")

    # ── Regelschleife ────────────────────────────────────────────────────────

    last_control_time = 0.0
    last_telemetry_time = 0.0
    last_site_state_time = 0.0
    last_cleanup_time = time.time()

    log.info("Regelschleife: %ds | Telemetrie: %ds",
             config.control_interval, config.telemetry_interval)

    while True:
        now = time.time()

        # Config alle 5 Minuten prüfen (YAML file watch)
        if config.refresh_if_needed(300):
            log.info("Config geändert — baue Drivers + Site neu auf")
            drivers = build_drivers(config.assets)
            site = build_site(config)
            db.publish_log("info", f"Config aktualisiert: {len(config.assets)} Geräte, "
                                   f"{len(site.loadpoints)} Ladepunkte")

        # Commands aus SQLite pollen (jede Sekunde)
        try:
            db.poll_commands()
        except Exception as e:
            log.debug("Command-Poll Fehler: %s", e)

        # Cleanup einmal täglich
        if now - last_cleanup_time > 86400:
            last_cleanup_time = now
            db.cleanup(config.retention_days)

        # ── Regelschleife (10s) ──────────────────────────────────────────────
        if now - last_control_time >= config.control_interval:
            last_control_time = now

            if demo_mode and demo_site:
                # Demo: Simulationsdaten erzeugen und in DB schreiben
                try:
                    demo_state = demo_site.update()
                    if now - last_site_state_time >= 10:
                        last_site_state_time = now
                        db.publish_site_state(demo_state)
                except Exception as e:
                    log.error("Demo-Regelschleife Fehler: %s", e)
            else:
                # 1. Alle Geräte abfragen
                for asset_id, drv in drivers.items():
                    try:
                        if hasattr(drv, "poll"):
                            drv.poll()
                        elif hasattr(drv, "poll_all"):
                            drv.poll_all()
                    except Exception as e:
                        log.debug("Poll-Fehler %s: %s", asset_id, e)

                # 2. Fahrzeug-SoC pollen
                for vid, vdrv in _vehicle_drivers.items():
                    try:
                        vdrv.poll()
                        for lp in (site.loadpoints if site else []):
                            if getattr(lp, '_vehicle_driver', None) is vdrv:
                                lp.vehicle_soc = vdrv.soc
                    except Exception as e:
                        log.debug("Vehicle-Poll %s: %s", vid, e)

                # 3. Forecast + Tariff pollen
                if site:
                    if site.solar_forecast:
                        try:
                            site.solar_forecast.poll()
                        except Exception as e:
                            log.debug("Forecast-Poll Fehler: %s", e)
                    if site.tariff:
                        try:
                            site.tariff.poll()
                        except Exception as e:
                            log.debug("Tariff-Poll Fehler: %s", e)
                        for lp in site.loadpoints:
                            lp.tariff = site.tariff

                # 4. Site Regelung
                if site:
                    try:
                        site_state = site.update()

                        # Site State alle 10s in DB schreiben (für Dashboard)
                        if now - last_site_state_time >= 10:
                            last_site_state_time = now
                            db.publish_site_state(site_state)

                        # Abgeschlossene Sessions speichern
                        for lp in site.loadpoints:
                            for session in lp.pop_completed_sessions():
                                db.write_session(session)
                                db.publish_log("info",
                                    f"Ladesession beendet: {session.get('energy_kwh', 0):.2f} kWh "
                                    f"in {session.get('duration_s', 0) / 60:.0f} Min")

                    except Exception as e:
                        log.error("Regelschleife Fehler: %s", e)

        # ── Telemetrie (30s) ─────────────────────────────────────────────────
        if now - last_telemetry_time >= config.telemetry_interval:
            last_telemetry_time = now
            all_metrics = []

            if demo_mode and demo_site:
                # Demo: Telemetrie aus Simulationsdaten
                all_metrics = demo_site.get_telemetry_metrics()
            else:
                for asset_id, drv in drivers.items():
                    try:
                        if hasattr(drv, "get_telemetry_metrics"):
                            metrics = drv.get_telemetry_metrics()
                            all_metrics.extend(metrics)
                    except Exception as e:
                        log.error("Telemetrie-Fehler für %s: %s", asset_id, e)

                # Forecast + Tarif Metriken
                if site and site.solar_forecast:
                    fc = site.solar_forecast
                    all_metrics.extend([
                        {"metric_type": "forecast_current_w", "value": fc.current_estimate_w, "unit": "W"},
                        {"metric_type": "forecast_today_kwh", "value": fc.today_kwh, "unit": "kWh"},
                        {"metric_type": "forecast_remaining_kwh", "value": fc.remaining_today_kwh, "unit": "kWh"},
                    ])
                if site and site.tariff:
                    tf = site.tariff
                    all_metrics.extend([
                        {"metric_type": "tariff_current_ct", "value": tf.current_price_ct, "unit": "ct/kWh"},
                    ])

            if all_metrics:
                db.publish_telemetry(all_metrics)

        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Wald EMS Client beendet")
        if db:
            db.disconnect()
        close_modbus()

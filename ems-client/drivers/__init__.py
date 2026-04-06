"""Driver Registry — Factory Pattern für Geräte-Treiber."""

from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger("ems.drivers")

# ── Registry ──────────────────────────────────────────────────────────────────

DRIVER_REGISTRY: dict[str, type] = {}


def register(name: str):
    """Decorator: Registriert eine Driver-Klasse unter einem Typ-Namen.

    Beispiel:
        @register("nrgkick_modbus")
        class NRGKickCharger(Charger, Meter):
            ...
    """
    def decorator(cls):
        DRIVER_REGISTRY[name] = cls
        log.debug("Driver registriert: %s → %s", name, cls.__name__)
        return cls
    return decorator


def create_driver(driver_type: str, config: dict[str, Any]):
    """Erstellt eine Driver-Instanz anhand des Typ-Strings aus der DB."""
    cls = DRIVER_REGISTRY.get(driver_type)
    if cls is None:
        raise ValueError(f"Unbekannter Driver-Typ: {driver_type!r}. "
                         f"Verfügbar: {', '.join(sorted(DRIVER_REGISTRY))}")
    return cls(config)


def list_drivers() -> list[str]:
    """Gibt alle registrierten Driver-Typen zurück."""
    return sorted(DRIVER_REGISTRY.keys())


# ── Auto-Import aller Driver-Module ──────────────────────────────────────────

_DRIVER_MODULES = [
    "drivers.modbus.generic",
    "drivers.victron.venus",
    "drivers.nrgkick.modbus",
    "drivers.sunspec.sunspec",
    "drivers.http_base",
    "drivers.knx.knx_meter",
    "drivers.mbus.mbus_meter",
    "drivers.heatpump.sg_ready",
    "drivers.vehicle.cloud_api",
]


def load_all_drivers():
    """Importiert alle bekannten Driver-Module, damit @register() ausgeführt wird."""
    for module_name in _DRIVER_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            log.warning("Driver-Modul %s nicht geladen: %s", module_name, e)

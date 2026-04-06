"""Lokaler Config-Cache für Offline-Betrieb."""

import json
import logging
import os

log = logging.getLogger("ems.cache")

CACHE_DIR = "/opt/ems"
CACHE_FILE = os.path.join(CACHE_DIR, "config_cache.json")


def save(data: dict) -> None:
    """Speichert Config lokal für Offline-Betrieb."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        log.debug("Config-Cache gespeichert: %s", CACHE_FILE)
    except Exception as e:
        log.warning("Config-Cache speichern fehlgeschlagen: %s", e)


def load() -> dict | None:
    """Lädt Config aus lokalem Cache. Gibt None zurück wenn nicht vorhanden."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        log.info("Config aus Cache geladen: %s", CACHE_FILE)
        return data
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("Config-Cache laden fehlgeschlagen: %s", e)
        return None

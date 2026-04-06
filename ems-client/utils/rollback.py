"""Rollback — Prüft ob ein fehlgeschlagenes Update rückgängig gemacht werden muss.

Wird von main.py beim Start aufgerufen.
Logik:
1. Wenn .update_pending existiert (= gerade upgedated):
   - Beim ERSTEN Start: Marker auf "started" setzen → kein Rollback
   - Beim ZWEITEN Start < 90s: Crash-Loop erkannt → Rollback!
2. Wenn kein Marker: nichts tun
3. Wenn Marker > 90s alt: Update war stabil → Marker + Backup löschen
"""

import logging
import os
import shutil
import time

log = logging.getLogger("ems.rollback")

EMS_DIR = "/opt/ems"
CLIENT_DIR = os.path.join(EMS_DIR, "ems-client")
BACKUP_DIR = os.path.join(EMS_DIR, "ems-client.bak")
MARKER_FILE = os.path.join(EMS_DIR, ".update_pending")
STARTED_FILE = os.path.join(EMS_DIR, ".update_started")


def check_rollback() -> bool:
    """Prüft ob ein Rollback nötig ist. Returns True wenn Rollback durchgeführt wurde."""
    if not os.path.exists(MARKER_FILE):
        # Kein Update ausstehend — alles gut
        # Started-Marker aufräumen falls vorhanden
        if os.path.exists(STARTED_FILE):
            os.remove(STARTED_FILE)
        return False

    try:
        marker_time = os.path.getmtime(MARKER_FILE)
    except Exception:
        marker_time = 0

    now = time.time()
    time_since_update = now - marker_time

    # Update älter als 120s → war erfolgreich
    if time_since_update > 120:
        log.info("Update erfolgreich bestätigt (%.0fs stabil)", time_since_update)
        _cleanup()
        return False

    # Innerhalb 120s nach Update — ist das der erste oder zweite Start?
    if not os.path.exists(STARTED_FILE):
        # ERSTER Start nach Update → normal (Restart durch Updater)
        # Markiere dass wir gestartet haben
        try:
            with open(STARTED_FILE, "w") as f:
                f.write(str(now))
        except Exception:
            pass
        log.info("Erster Start nach Update (%.0fs) — beobachte...", time_since_update)
        return False
    else:
        # ZWEITER Start innerhalb 120s → Crash-Loop!
        if os.path.exists(BACKUP_DIR):
            log.warning("Crash-Loop erkannt (%.0fs, 2. Start) — Rollback!", time_since_update)
            try:
                if os.path.exists(CLIENT_DIR):
                    shutil.rmtree(CLIENT_DIR)
                shutil.copytree(BACKUP_DIR, CLIENT_DIR)
                _cleanup()
                log.warning("Rollback erfolgreich — alte Version wiederhergestellt")
                return True
            except Exception as e:
                log.error("Rollback fehlgeschlagen: %s", e)
                return False
        else:
            log.warning("Crash-Loop erkannt, aber kein Backup vorhanden")
            _cleanup()
            return False


def confirm_stable():
    """Wird nach 120s stabilem Betrieb aufgerufen — räumt Marker auf."""
    if os.path.exists(MARKER_FILE):
        log.info("Update stabil — Backup wird gelöscht")
        _cleanup()


def _cleanup():
    """Räumt Marker und Backup auf."""
    for f in (MARKER_FILE, STARTED_FILE):
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass
    if os.path.exists(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)

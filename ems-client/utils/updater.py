"""Self-Update — Lädt neue Version von Supabase Storage.

Ablauf:
1. Download tar.gz → /tmp/
2. Prüfe neue Version
3. Backup aktuelles ems-client/ → ems-client.bak/
4. Entpacke neue Version
5. Schreibe .update_pending Marker
6. Neustart via systemctl
7. Bei Crash < 90s → Rollback durch rollback.py
"""

import logging
import os
import shutil
import time

log = logging.getLogger("ems.updater")

DOWNLOAD_URL = "https://grdpcosbrvxuzgqigdwc.supabase.co/storage/v1/object/public/pi-releases/ems-client.tar.gz"
EMS_DIR = "/opt/ems"
CLIENT_DIR = os.path.join(EMS_DIR, "ems-client")
BACKUP_DIR = os.path.join(EMS_DIR, "ems-client.bak")
MARKER_FILE = os.path.join(EMS_DIR, ".update_pending")

# Auto-Update: alle 6 Stunden prüfen
AUTO_UPDATE_INTERVAL = 6 * 3600


def update_client(publish_log_fn=None, current_version: str = ""):
    """Lädt die neueste Version und startet neu mit Auto-Rollback."""
    import urllib.request
    import tarfile
    import tempfile

    def _log(level, msg):
        log.info(msg) if level == "info" else log.error(msg)
        if publish_log_fn:
            try:
                publish_log_fn(level, msg, wait=True)
            except TypeError:
                publish_log_fn(level, msg)

    _log("info", f"Update gestartet — aktuelle Version: {current_version}")

    try:
        # 1. Download
        tmp_path = os.path.join(tempfile.gettempdir(), "ems-client-update.tar.gz")
        _log("info", "Lade Update herunter...")
        urllib.request.urlretrieve(DOWNLOAD_URL, tmp_path)

        file_size = os.path.getsize(tmp_path)
        _log("info", f"Download abgeschlossen ({file_size // 1024} KB)")

        # 2. Neue Version prüfen
        new_version = ""
        with tarfile.open(tmp_path, "r:gz") as tar:
            try:
                for member in tar.getmembers():
                    if member.name.endswith("db_handler.py"):
                        f = tar.extractfile(member)
                        if f:
                            content = f.read().decode("utf-8", errors="replace")
                            for line in content.splitlines():
                                stripped = line.strip()
                                if stripped.startswith("VERSION") and "=" in stripped and '"' in stripped:
                                    new_version = stripped.split('"')[1]
                                    break
                        break
            except Exception as e:
                log.debug("Versionscheck fehlgeschlagen: %s", e)

        if new_version and new_version == current_version:
            os.remove(tmp_path)
            _log("info", f"Bereits auf dem neuesten Stand (v{current_version})")
            return

        _log("info", f"Neue Version gefunden: v{new_version or '?'} — installiere...")

        # 3. Backup erstellen
        if os.path.exists(BACKUP_DIR):
            shutil.rmtree(BACKUP_DIR)
        if os.path.exists(CLIENT_DIR):
            shutil.copytree(CLIENT_DIR, BACKUP_DIR)
            _log("info", "Backup erstellt")

        # 4. Neue Version entpacken
        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(path=os.path.dirname(CLIENT_DIR))

        os.remove(tmp_path)

        # 4b. pip install (neue Dependencies installieren)
        req_file = os.path.join(CLIENT_DIR, "requirements.txt")
        venv_pip = os.path.join(EMS_DIR, "venv", "bin", "pip")
        if os.path.exists(req_file) and os.path.exists(venv_pip):
            _log("info", "Installiere Python-Pakete...")
            import subprocess
            try:
                result = subprocess.run(
                    [venv_pip, "install", "-r", req_file],
                    capture_output=True, text=True, timeout=300,
                    cwd=CLIENT_DIR,
                    env={**os.environ, "PATH": f"{EMS_DIR}/venv/bin:{os.environ.get('PATH', '')}"},
                )
                if result.returncode == 0:
                    installed = [l for l in result.stdout.splitlines() if "Successfully" in l or "installed" in l.lower()]
                    _log("info", f"Python-Pakete OK" + (f": {installed[0]}" if installed else ""))
                else:
                    err = result.stderr.strip().split("\n")[-1] if result.stderr else "unbekannt"
                    _log("warning", f"pip install Warnung: {err}")
            except subprocess.TimeoutExpired:
                _log("warning", "pip install Timeout (300s) — wird beim nächsten Start nachgeholt")
            except Exception as e:
                _log("warning", f"pip install fehlgeschlagen: {e}")

        # 5. Rollback-Marker schreiben
        with open(MARKER_FILE, "w") as f:
            f.write(f"{time.time()}\n{current_version}\n{new_version}\n")

        _log("info", f"Update erfolgreich: v{current_version} → v{new_version or '?'} — Neustart in 3s (Auto-Rollback aktiv)")

        # 6. Neustart
        time.sleep(3)
        os.system("systemctl restart ems-client")

    except Exception as e:
        _log("error", f"Update fehlgeschlagen: {e}")
        # Bei Fehler: Backup wiederherstellen falls vorhanden
        if os.path.exists(BACKUP_DIR) and not os.path.exists(os.path.join(CLIENT_DIR, "main.py")):
            _log("info", "Stelle Backup wieder her...")
            if os.path.exists(CLIENT_DIR):
                shutil.rmtree(CLIENT_DIR)
            shutil.copytree(BACKUP_DIR, CLIENT_DIR)
            _log("info", "Backup wiederhergestellt")


def check_for_update(current_version: str) -> str | None:
    """Prüft ob eine neue Version verfügbar ist OHNE zu installieren.
    Returns die neue Version oder None.
    """
    import urllib.request
    import tarfile
    import tempfile

    try:
        tmp_path = os.path.join(tempfile.gettempdir(), "ems-client-check.tar.gz")
        urllib.request.urlretrieve(DOWNLOAD_URL, tmp_path)

        new_version = ""
        with tarfile.open(tmp_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("db_handler.py"):
                    f = tar.extractfile(member)
                    if f:
                        content = f.read().decode("utf-8", errors="replace")
                        for line in content.splitlines():
                            stripped = line.strip()
                            if stripped.startswith("VERSION") and "=" in stripped and '"' in stripped:
                                new_version = stripped.split('"')[1]
                                break
                    break

        os.remove(tmp_path)

        if new_version and new_version != current_version:
            return new_version
        return None

    except Exception:
        return None

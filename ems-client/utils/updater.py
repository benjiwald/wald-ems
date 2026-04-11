"""Self-Update — Lädt neue Version von GitHub.

Ablauf:
1. Versionsprüfung via GitHub Raw Content (nur db_handler.py)
2. Download Repo-Archiv von GitHub
3. Backup aktuelles ems-client/ → ems-client.bak/
4. Entpacke ems-client/ aus dem Archiv
5. Schreibe .update_pending Marker
6. Neustart via systemctl
7. Bei Crash < 90s → Rollback durch rollback.py
"""

import logging
import os
import shutil
import time

log = logging.getLogger("ems.updater")

GITHUB_REPO = "benjiwald/wald-ems"
GITHUB_BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/ems-client/db_handler.py"
ARCHIVE_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.tar.gz"

EMS_DIR = "/opt/ems"
CLIENT_DIR = os.path.join(EMS_DIR, "ems-client")
BACKUP_DIR = os.path.join(EMS_DIR, "ems-client.bak")
MARKER_FILE = os.path.join(EMS_DIR, ".update_pending")

# Auto-Update: alle 6 Stunden prüfen
AUTO_UPDATE_INTERVAL = 6 * 3600


def _parse_version(content: str) -> str:
    """Extrahiert VERSION = "x.y.z" aus Python-Quelltext."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("VERSION") and "=" in stripped and '"' in stripped:
            return stripped.split('"')[1]
    return ""


def update_client(publish_log_fn=None, current_version: str = ""):
    """Lädt die neueste Version von GitHub und startet neu mit Auto-Rollback."""
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
        # 1. Versionsprüfung (nur db_handler.py laden, nicht ganzes Archiv)
        _log("info", "Prüfe Version auf GitHub...")
        req = urllib.request.Request(VERSION_URL)
        with urllib.request.urlopen(req, timeout=15) as resp:
            version_content = resp.read().decode("utf-8", errors="replace")
        new_version = _parse_version(version_content)

        if new_version and new_version == current_version:
            _log("info", f"Bereits auf dem neuesten Stand (v{current_version})")
            return

        _log("info", f"Neue Version gefunden: v{new_version or '?'} — lade herunter...")

        # 2. Archiv herunterladen
        tmp_path = os.path.join(tempfile.gettempdir(), "ems-client-update.tar.gz")
        urllib.request.urlretrieve(ARCHIVE_URL, tmp_path)

        file_size = os.path.getsize(tmp_path)
        _log("info", f"Download abgeschlossen ({file_size // 1024} KB)")

        # 3. Backup erstellen
        if os.path.exists(BACKUP_DIR):
            shutil.rmtree(BACKUP_DIR)
        if os.path.exists(CLIENT_DIR):
            shutil.copytree(CLIENT_DIR, BACKUP_DIR)
            _log("info", "Backup erstellt")

        # 4. ems-client/ aus dem GitHub-Archiv entpacken
        #    GitHub-Archiv Struktur: wald-ems-main/ems-client/...
        #    → wir müssen den Prefix strippen
        with tarfile.open(tmp_path, "r:gz") as tar:
            # Prefix ermitteln (erster Ordner im Archiv, z.B. "wald-ems-main")
            members = tar.getmembers()
            prefix = members[0].name.split("/")[0] if members else ""
            ems_prefix = f"{prefix}/ems-client/"

            # Nur ems-client/ Dateien extrahieren, Pfad anpassen
            for member in members:
                if member.name.startswith(ems_prefix):
                    # Prefix strippen: "wald-ems-main/ems-client/foo" → "ems-client/foo"
                    member.name = member.name[len(prefix) + 1:]
                    tar.extract(member, path=EMS_DIR)

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

    Lädt nur db_handler.py von GitHub Raw (~1 KB statt ganzes Archiv).
    Returns die neue Version oder None.
    """
    import urllib.request

    try:
        req = urllib.request.Request(VERSION_URL)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        new_version = _parse_version(content)

        if new_version and new_version != current_version:
            return new_version
        return None

    except Exception:
        return None

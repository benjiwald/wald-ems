"""DB Handler — SQLite-basierte Kommunikation (ersetzt MQTT)."""

import json
import logging
import sqlite3
import os
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger("ems.db")

VERSION = "1.0.9"


class DBHandler:
    """SQLite-basierter Handler — ersetzt MQTTHandler für lokalen Betrieb.

    Schreibt State/Telemetrie in SQLite, pollt Commands aus SQLite.
    Wird von main.py genauso genutzt wie vorher MQTTHandler.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._command_handler: Callable[[dict], None] | None = None
        self._connected = True  # Lokal immer "connected"
        self._init_db()

    def _init_db(self):
        """Erstellt Tabellen falls nötig (idempotent)."""
        migrations_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        if os.path.exists(migrations_dir):
            for f in sorted(os.listdir(migrations_dir)):
                if f.endswith(".sql"):
                    sql = open(os.path.join(migrations_dir, f)).read()
                    conn.executescript(sql)
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    # ── Command Handling ─────────────────────────────────────────────────────

    def on_command(self, handler: Callable[[dict], None]):
        self._command_handler = handler

    def on_config(self, handler: Callable[[dict], None]):
        pass  # Config kommt aus YAML, nicht aus DB

    def poll_commands(self):
        """Prüft auf pending Commands und verarbeitet sie."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, action, payload FROM commands WHERE status = 'pending' ORDER BY created_at ASC LIMIT 10"
            ).fetchall()

            for row in rows:
                cmd_id, action, payload_str = row
                conn.execute("UPDATE commands SET status = 'processing' WHERE id = ?", (cmd_id,))
                conn.commit()

                try:
                    payload = json.loads(payload_str) if payload_str else {}
                    payload["action"] = action
                    if self._command_handler:
                        self._command_handler(payload)
                    conn.execute(
                        "UPDATE commands SET status = 'done', processed_at = datetime('now') WHERE id = ?",
                        (cmd_id,),
                    )
                except Exception as e:
                    conn.execute(
                        "UPDATE commands SET status = 'error', result = ?, processed_at = datetime('now') WHERE id = ?",
                        (str(e), cmd_id),
                    )
                conn.commit()
        finally:
            conn.close()

    # ── Publish (schreibt in SQLite statt MQTT) ─────────────────────────────

    def publish_site_state(self, state: dict):
        """Schreibt aktuellen Site-State in die state-Tabelle."""
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            state["updated_at"] = now
            conn.execute(
                "INSERT INTO state (key, value, updated_at) VALUES ('site_state', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (json.dumps(state), now),
            )
            conn.commit()
        finally:
            conn.close()

    def publish_telemetry(self, metrics: list[dict]):
        """Schreibt Telemetrie-Metriken in die telemetry-Tabelle."""
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for m in metrics:
                metric = m.get("metric_type", "unknown")
                value = m.get("value", 0)
                unit = m.get("unit", "")
                conn.execute(
                    "INSERT INTO telemetry (metric, value, unit, timestamp) VALUES (?, ?, ?, ?)",
                    (metric, value, unit, now),
                )
            conn.commit()
            log.info("Telemetrie geschrieben: %d Metriken", len(metrics))
        finally:
            conn.close()

    def publish_log(self, level: str, message: str, metadata: dict | None = None,
                    wait: bool = False):
        """Schreibt Log in die device_logs-Tabelle."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO device_logs (level, source, message, metadata) VALUES (?, 'ems-client', ?, ?)",
                (level, message, json.dumps(metadata) if metadata else None),
            )
            conn.commit()
        finally:
            conn.close()
        # Auch ins Python-Log
        getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(message)

    def publish_status(self, status: str):
        """Schreibt Client-Status in die state-Tabelle."""
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO state (key, value, updated_at) VALUES ('client_status', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (json.dumps({"status": status, "version": VERSION, "ts": now}), now),
            )
            conn.commit()
        finally:
            conn.close()

    def write_session(self, session: dict):
        """Schreibt abgeschlossene Ladesession in die DB."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO charging_sessions (loadpoint, started_at, finished_at, energy_kwh, solar_kwh, "
                "max_power_w, avg_power_w, mode, phases, vehicle, cost_eur) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.get("loadpoint_name", ""),
                    session.get("started_at", ""),
                    session.get("finished_at", ""),
                    session.get("energy_kwh", 0),
                    session.get("solar_kwh", 0),
                    session.get("max_power_w", 0),
                    session.get("avg_power_w", 0),
                    session.get("mode", ""),
                    session.get("phases", 1),
                    session.get("vehicle", ""),
                    session.get("cost_eur", 0),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def cleanup(self, retention_days: int = 30):
        """Löscht alte Daten."""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM telemetry WHERE timestamp < datetime('now', ?)", (f"-{retention_days} days",))
            conn.execute("DELETE FROM device_logs WHERE created_at < datetime('now', ?)", (f"-{retention_days} days",))
            conn.execute("DELETE FROM commands WHERE status = 'done' AND created_at < datetime('now', '-7 days')")
            conn.commit()
            log.info("Datenbank bereinigt (Retention: %d Tage)", retention_days)
        finally:
            conn.close()

    # Kompatibilität mit altem Code
    def connect(self):
        self.publish_status("online")

    def disconnect(self):
        self.publish_status("offline")

    def replay_buffer(self):
        pass  # Kein Buffer nötig bei lokalem SQLite

    def publish(self, topic: str, payload: dict):
        pass  # Nicht benötigt lokal

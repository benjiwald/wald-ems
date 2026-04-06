"""Telemetry Buffer — Puffert Messdaten bei Offline und sendet sie nach Reconnect.

Speichert Telemetrie-Nachrichten als JSON Lines in einer lokalen Datei.
Beim nächsten erfolgreichen MQTT-Connect werden die gepufferten Daten gesendet.

Max Buffer: 10.000 Zeilen (~5MB) = ~3.5 Tage bei 30s Intervall
"""

import json
import logging
import os
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("ems.buffer")

BUFFER_DIR = "/opt/ems/buffer"
BUFFER_FILE = os.path.join(BUFFER_DIR, "telemetry.jsonl")
MAX_LINES = 10_000
REPLAY_BATCH = 50  # Nachrichten pro Replay-Zyklus


class TelemetryBuffer:
    """Puffert Telemetrie bei Offline, replayed bei Reconnect."""

    def __init__(self):
        os.makedirs(BUFFER_DIR, exist_ok=True)
        self._queue: deque[str] = deque(maxlen=MAX_LINES)
        self._load_from_disk()
        self._is_online = True

    def _load_from_disk(self):
        """Lädt gepufferte Nachrichten von der Festplatte."""
        if not os.path.exists(BUFFER_FILE):
            return
        try:
            with open(BUFFER_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._queue.append(line)
            if self._queue:
                log.info("Buffer: %d Nachrichten von Disk geladen", len(self._queue))
        except Exception as e:
            log.warning("Buffer laden fehlgeschlagen: %s", e)

    def _save_to_disk(self):
        """Schreibt den aktuellen Buffer auf die Festplatte."""
        try:
            with open(BUFFER_FILE, "w") as f:
                for line in self._queue:
                    f.write(line + "\n")
        except Exception as e:
            log.debug("Buffer speichern fehlgeschlagen: %s", e)

    def add(self, payload: dict):
        """Fügt eine Nachricht zum Buffer hinzu."""
        line = json.dumps(payload, separators=(",", ":"))
        self._queue.append(line)
        # Periodisch auf Disk schreiben (alle 10 Nachrichten)
        if len(self._queue) % 10 == 0:
            self._save_to_disk()

    def set_online(self, online: bool):
        """Setzt den Online-Status."""
        if online and not self._is_online:
            log.info("Buffer: Wieder online — %d Nachrichten zum Replay", len(self._queue))
        self._is_online = online

    def has_pending(self) -> bool:
        """Ob gepufferte Nachrichten zum Senden vorhanden sind."""
        return len(self._queue) > 0

    def get_replay_batch(self) -> list[dict]:
        """Gibt einen Batch gepufferter Nachrichten zurück (FIFO)."""
        batch = []
        for _ in range(min(REPLAY_BATCH, len(self._queue))):
            try:
                line = self._queue.popleft()
                batch.append(json.loads(line))
            except (json.JSONDecodeError, IndexError):
                continue
        if batch:
            self._save_to_disk()
        return batch

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def flush(self):
        """Schreibt alles auf Disk (beim Shutdown)."""
        self._save_to_disk()

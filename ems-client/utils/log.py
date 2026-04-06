"""Logging — Journal + MQTT Dual-Output."""

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("ems")

_mqtt_client = None
_hardware_id = ""
_topic_logs = ""


def init_mqtt_logging(mqtt_client, hardware_id: str):
    """Initialisiert MQTT-Logging (aufgerufen nach MQTT-Verbindung)."""
    global _mqtt_client, _hardware_id, _topic_logs
    _mqtt_client = mqtt_client
    _hardware_id = hardware_id
    _topic_logs = f"ems/{hardware_id}/logs"


def publish_log(level: str, message: str, metadata: dict | None = None):
    """Sendet Log-Eintrag an Dashboard via MQTT."""
    if _mqtt_client is None:
        return
    payload = json.dumps({
        "level":    level,
        "message":  message,
        "metadata": metadata or {},
        "ts":       datetime.now(timezone.utc).isoformat(),
    })
    _mqtt_client.publish(_topic_logs, payload, qos=0)

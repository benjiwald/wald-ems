"""GenericModbusDevice — Backward-kompatibel mit bestehendem Register-Map-System.

Liest beliebige Register Maps aus der DB (wie der alte poller.py).
Wird als Fallback verwendet, wenn kein spezifischer Driver konfiguriert ist.
"""

import logging
from api.meter import Meter, MeterEnergy
from drivers import register
from drivers.modbus.connection import get_connection

log = logging.getLogger("ems.drivers.generic")


@register("generic_modbus")
class GenericModbusDevice(Meter):
    """Generischer Modbus-Treiber: liest jede Register Map aus der DB."""

    def __init__(self, config: dict):
        conn_params = config.get("connection_params") or {}
        self.host = conn_params.get("host", "")
        self.port = int(conn_params.get("port", 502))
        self.unit_id = int(conn_params.get("unit_id", 1))
        self.timeout = int(conn_params.get("timeout_ms", 3000)) / 1000
        self.register_map: dict = config.get("modbus_register_map") or {}
        self.name = config.get("name", "generic")
        self._last_metrics: dict[str, float] = {}

    def current_power(self) -> float:
        """Liest 'power' oder erstes Register mit 'power' im Key."""
        self.poll()
        for key in ("power", "grid_power", "pv_power", "charging_power"):
            if key in self._last_metrics:
                return self._last_metrics[key]
        return 0.0

    def poll(self) -> dict[str, float]:
        """Pollt alle Register und gibt dict {metric_key: value} zurück."""
        if not self.host or self.host.startswith("192.168.x"):
            return {}
        if not self.register_map:
            return {}

        conn = get_connection(self.host, self.port, self.unit_id, self.timeout)
        metrics: dict[str, float] = {}

        for key, reg in self.register_map.items():
            # Per-Register unit_id Override (z.B. MPPT 237/238 auf Venus OS)
            reg_unit_id = int(reg.get("unit_id", self.unit_id))
            value = conn.read_register(
                address=reg["address"],
                reg_type=reg.get("type", "uint16"),
                scale=float(reg.get("scale", 1)),
                unit_id=reg_unit_id,
                func=reg.get("func", "holding"),
                word_order=reg.get("word_order", "msw"),
            )
            if value is not None:
                metric_key = reg.get("metric_key", key)
                metrics[metric_key] = value

        self._last_metrics = metrics
        log.debug("Asset %s: %d Metriken gelesen", self.name, len(metrics))
        return metrics

    def get_telemetry_metrics(self) -> list[dict]:
        """Gibt gecachte Metriken vom letzten poll() zurück."""
        # Nutzt _last_metrics statt erneut zu pollen (poll() läuft in der Regelschleife)
        if not self._last_metrics:
            self.poll()  # Fallback falls noch nie gepollt
        result = []
        for key, reg in self.register_map.items():
            metric_key = reg.get("metric_key", key)
            if metric_key in self._last_metrics:
                result.append({
                    "metric_type": metric_key,
                    "value": self._last_metrics[metric_key],
                    "unit": reg.get("unit", ""),
                })
        return result

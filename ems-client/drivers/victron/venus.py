"""Victron Venus OS — Modbus TCP Driver.

Liest Register-Adressen aus der DB-Config (modbus_register_map).
Fügt spezifische Methoden für Energiebilanz hinzu (grid, battery, pv, consumption).

Register-Map kommt aus der assets-Tabelle, z.B.:
  grid_power_l1:     {address: 820, type: int16, scale: 1, metric_key: grid_power}
  grid_power_l2:     {address: 821, type: int16, scale: 1}
  battery_soc:       {address: 843, type: uint16, scale: 1}
  battery_power:     {address: 842, type: int16, scale: 1}
  ac_consumption_l1: {address: 817, type: uint16, scale: 1}
"""

import logging
from api.meter import Meter
from api.battery import Battery
from api.interfaces import PhasePowers
from drivers import register
from drivers.modbus.connection import get_connection, ModbusConnection

log = logging.getLogger("ems.drivers.victron")


@register("victron_venus_system")
class VenusOSSystem(Meter, Battery, PhasePowers):
    """Venus OS System-Aggregator — liest Register aus DB-Config."""

    def __init__(self, config: dict):
        conn_params = config.get("connection_params") or {}
        self.host = conn_params.get("host", "")
        self.port = int(conn_params.get("port", 502))
        self.unit_id = int(conn_params.get("unit_id", 100))
        self.timeout = int(conn_params.get("timeout_ms", 3000)) / 1000
        self.name = config.get("name", "Venus OS")
        self.register_map: dict = config.get("modbus_register_map") or {}

        # MPPT-Tracker (separate Assets, optional via config)
        self.mppt_units: list[dict] = config.get("mppt_units") or []

        self._conn: ModbusConnection | None = None
        self._cache: dict[str, float] = {}

    def _get_conn(self) -> ModbusConnection:
        if self._conn is None:
            self._conn = get_connection(self.host, self.port, self.unit_id, self.timeout)
        return self._conn

    def _read_reg(self, key: str) -> float:
        """Liest ein Register aus der DB-Config anhand des Keys."""
        reg = self.register_map.get(key)
        if not reg:
            return 0.0
        val = self._get_conn().read_register(
            address=reg["address"],
            reg_type=reg.get("type", "uint16"),
            scale=float(reg.get("scale", 1)),
            unit_id=self.unit_id,
        )
        return val if val is not None else 0.0

    # ── Meter Interface ───────────────────────────────────────────────────────

    def current_power(self) -> float:
        """Grid Power L1+L2+L3 in Watt."""
        l1 = self._read_reg("grid_power_l1")
        l2 = self._read_reg("grid_power_l2")
        l3 = self._read_reg("grid_power_l3")
        mk1 = self.register_map.get("grid_power_l1", {}).get("metric_key", "grid_power")
        self._cache[mk1] = l1
        self._cache["grid_power_l2"] = l2
        self._cache["grid_power_l3"] = l3
        return l1 + l2 + l3

    # ── Battery Interface ─────────────────────────────────────────────────────

    def soc(self) -> float:
        val = self._read_reg("battery_soc")
        self._cache["battery_soc"] = val
        return val

    def current_power(self) -> float:
        """Grid Power — aber Battery braucht auch current_power.
        Rufe explizit grid_power() oder battery_power() auf."""
        # Default: Grid Power
        return self.grid_power()

    def grid_power(self) -> float:
        l1 = self._read_reg("grid_power_l1")
        l2 = self._read_reg("grid_power_l2")
        l3 = self._read_reg("grid_power_l3")
        mk1 = self.register_map.get("grid_power_l1", {}).get("metric_key", "grid_power")
        self._cache[mk1] = l1
        self._cache["grid_power_l2"] = l2
        self._cache["grid_power_l3"] = l3
        return l1 + l2 + l3

    def battery_power(self) -> float:
        val = self._read_reg("battery_power")
        self._cache["battery_power"] = val
        return val

    # ── PhasePowers Interface ─────────────────────────────────────────────────

    def powers(self) -> tuple[float, float, float]:
        return (
            self._cache.get("grid_power", 0),
            self._cache.get("grid_power_l2", 0),
            self._cache.get("grid_power_l3", 0),
        )

    # ── Erweiterte Methoden ───────────────────────────────────────────────────

    def consumption_power(self) -> float:
        l1 = self._read_reg("ac_consumption_l1")
        l2 = self._read_reg("ac_consumption_l2")
        l3 = self._read_reg("ac_consumption_l3")
        self._cache["ac_consumption_l1"] = l1
        self._cache["ac_consumption_l2"] = l2
        self._cache["ac_consumption_l3"] = l3
        return l1 + l2 + l3

    def battery_voltage(self) -> float:
        val = self._read_reg("battery_voltage")
        self._cache["battery_voltage"] = val
        return val

    def battery_current(self) -> float:
        val = self._read_reg("battery_current")
        self._cache["battery_current"] = val
        return val

    def pv_power_mppt(self) -> float:
        """Liest PV von konfigurierten MPPT-Trackern (separate Assets)."""
        total = 0.0
        for i, mppt in enumerate(self.mppt_units, 1):
            uid = mppt["unit_id"]
            reg = mppt.get("register", 786)
            val = self._get_conn().read_register(reg, "uint16", 1.0, uid)
            key = f"pv_mppt_{i}"
            self._cache[key] = val if val is not None else 0.0
            total += self._cache[key]
        return total

    # ── Telemetrie ────────────────────────────────────────────────────────────

    def poll_all(self) -> dict[str, float]:
        """Pollt alle Register aus der DB-Config."""
        for key, reg in self.register_map.items():
            val = self._read_reg(key)
            metric_key = reg.get("metric_key", key)
            self._cache[metric_key] = val
        # MPPT separat
        self.pv_power_mppt()
        return dict(self._cache)

    def get_telemetry_metrics(self) -> list[dict]:
        data = self.poll_all()
        result = []
        # Register-Map Metriken
        for key, reg in self.register_map.items():
            metric_key = reg.get("metric_key", key)
            if metric_key in data:
                result.append({
                    "metric_type": metric_key,
                    "value": data[metric_key],
                    "unit": reg.get("unit", ""),
                })
        # MPPT Metriken
        for i in range(1, len(self.mppt_units) + 1):
            k = f"pv_mppt_{i}"
            if k in data:
                result.append({"metric_type": k, "value": data[k], "unit": "W"})
        return result

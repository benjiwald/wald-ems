"""Victron Venus OS — Modbus TCP Driver.

Liest Register-Adressen aus der DB-Config (modbus_register_map).
Fügt spezifische Methoden für Energiebilanz hinzu (grid, battery, pv, consumption).

Register-Map kommt aus der assets-Tabelle, z.B.:
  grid_power_l1:     {address: 820, type: int16, scale: 1, metric_key: grid_w}
  grid_power_l2:     {address: 821, type: int16, scale: 1}
  battery_soc:       {address: 843, type: uint16, scale: 1}
  battery_power:     {address: 842, type: int16, scale: 1}
  ac_consumption_l1: {address: 817, type: uint16, scale: 1}
  pv_power:          {address: 850, type: uint16, scale: 1}
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
    """Venus OS System-Aggregator — liest Register aus DB-Config.

    Implementiert Meter, Battery UND PhasePowers Interface.
    site.py liest bevorzugt aus _last_metrics (nach poll_all()),
    Fallback über die einzelnen Methoden.
    """

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
        # _last_metrics: wird von site.py gelesen (primärer Pfad)
        self._last_metrics: dict[str, float] = {}

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

    # ── Meter Interface (Grid Power) ─────────────────────────────────────────

    def current_power(self) -> float:
        """Grid Power L1+L2+L3 in Watt (Meter Interface)."""
        return self.grid_power()

    # ── Battery Interface ─────────────────────────────────────────────────────

    def soc(self) -> float:
        val = self._read_reg("battery_soc")
        self._cache["battery_soc"] = val
        return val

    # NOTE: Battery.current_power() wuerde auch grid_power() aufrufen (da
    # Python nur EINE current_power() haben kann). Deshalb liest site.py
    # battery_power direkt aus _last_metrics["battery_power"].
    # Fallback _read_battery() nutzt battery_power() explizit.

    def grid_power(self) -> float:
        """Grid Power L1+L2+L3."""
        l1 = self._read_reg("grid_power_l1")
        l2 = self._read_reg("grid_power_l2")
        l3 = self._read_reg("grid_power_l3")
        self._cache["grid_power"] = l1 + l2 + l3
        self._cache["grid_power_l1"] = l1
        self._cache["grid_power_l2"] = l2
        self._cache["grid_power_l3"] = l3
        return l1 + l2 + l3

    def battery_power(self) -> float:
        """Battery Power in Watt (positiv=laden, negativ=entladen)."""
        val = self._read_reg("battery_power")
        self._cache["battery_power"] = val
        return val

    # ── PhasePowers Interface ─────────────────────────────────────────────────

    def powers(self) -> tuple[float, float, float]:
        return (
            self._cache.get("grid_power_l1", 0),
            self._cache.get("grid_power_l2", 0),
            self._cache.get("grid_power_l3", 0),
        )

    # ── Erweiterte Methoden ───────────────────────────────────────────────────

    def consumption_power(self) -> float:
        """AC Consumption L1+L2+L3."""
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

    def pv_power(self) -> float:
        """PV Power — DC (Reg 850) + AC-Out (808-810) + AC-In (811-813)."""
        # DC PV (MPPT-Tracker, Register 850)
        dc_pv = self._read_reg("pv_dc_power")
        # AC PV auf AC-Out (z.B. Fronius am AC-Ausgang des MultiPlus)
        ac_out = (self._read_reg("pv_acout_l1") +
                  self._read_reg("pv_acout_l2") +
                  self._read_reg("pv_acout_l3"))
        # AC PV auf AC-In (z.B. PV-Wechselrichter am Netz-Eingang)
        ac_in = (self._read_reg("pv_acin_l1") +
                 self._read_reg("pv_acin_l2") +
                 self._read_reg("pv_acin_l3"))
        total = dc_pv + ac_out + ac_in
        self._cache["pv_dc_total"] = dc_pv
        self._cache["pv_ac_total"] = ac_out + ac_in
        self._cache["pv_power"] = total
        return total

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
        if total > 0:
            self._cache["pv_power"] = total
        return total

    # ── Telemetrie & Polling ─────────────────────────────────────────────────

    def poll_all(self) -> dict[str, float]:
        """Pollt alle Register und baut _last_metrics für site.py auf."""
        # Alle Register aus Config lesen
        for key, reg in self.register_map.items():
            val = self._read_reg(key)
            metric_key = reg.get("metric_key", key)
            self._cache[metric_key] = val

        # Grid Power aggregiert berechnen
        # metric_key für grid_power_l1 ist "grid_w", für L2/L3 bleibt der key
        grid_l1 = self._cache.get("grid_w", 0) or self._cache.get("grid_power_l1", 0)
        grid_l2 = self._cache.get("grid_power_l2", 0)
        grid_l3 = self._cache.get("grid_power_l3", 0)
        grid_total = grid_l1 + grid_l2 + grid_l3
        self._cache["grid_power_total"] = grid_total  # site.py liest diesen Key zuerst
        self._cache["grid_power"] = grid_total
        self._cache["grid_power_l1"] = grid_l1

        # Consumption aggregiert
        # metric_key für ac_consumption_l1 ist "consumption_w"
        cons_l1 = self._cache.get("consumption_w", 0) or self._cache.get("ac_consumption_l1", 0)
        cons_l2 = self._cache.get("ac_consumption_l2", 0)
        cons_l3 = self._cache.get("ac_consumption_l3", 0)
        cons_total = cons_l1 + cons_l2 + cons_l3
        self._cache["consumption"] = cons_total  # site.py liest diesen Key
        self._cache["ac_consumption_l1"] = cons_l1
        self._cache["ac_consumption_l2"] = cons_l2
        self._cache["ac_consumption_l3"] = cons_l3

        # Battery: metric_key ist "battery_w", aber site.py liest "battery_power"
        bat_power = self._cache.get("battery_w", 0) or self._cache.get("battery_power", 0)
        self._cache["battery_power"] = bat_power

        # Battery SoC: metric_key ist schon "battery_soc" → passt

        # PV: DC (Reg 850, metric_key pv_dc_total) + AC-Out (808-810) + AC-In (811-813)
        dc_pv = self._cache.get("pv_dc_total", 0) or 0
        ac_pv = (
            (self._cache.get("pv_acout_l1", 0) or 0) +
            (self._cache.get("pv_acout_l2", 0) or 0) +
            (self._cache.get("pv_acout_l3", 0) or 0) +
            (self._cache.get("pv_acin_l1", 0) or 0) +
            (self._cache.get("pv_acin_l2", 0) or 0) +
            (self._cache.get("pv_acin_l3", 0) or 0)
        )
        # MPPT-Tracker (wenn konfiguriert)
        mppt_total = self.pv_power_mppt()
        self._cache["pv_power"] = dc_pv + ac_pv + mppt_total

        # _last_metrics aktualisieren — site.py liest hieraus
        self._last_metrics = dict(self._cache)

        log.debug("Venus poll_all: %s", {k: round(v, 1) for k, v in self._last_metrics.items()})
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

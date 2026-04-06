"""NRG Kick Connect / PRO — Modbus TCP Driver (Gen2 API).

Register Map basierend auf der offiziellen NRG Kick Gen2 Local API Dokumentation:
https://nrgkick.com/wp-content/uploads/local_api_docu_simulate-1.html

Wichtig:
- Alle Register sind Holding Registers (Function Code 0x03)
- Little-Endian: uint16 = normal, int32/uint32 = LSW-first (Low Word zuerst)
- Unit ID: immer 1
- SmartModule Firmware >= 4.0.0.0 erforderlich

Control Registers (R/W):
  194: Charging amperage        (uint16, factor 10)    → 6.0-32.0A
  195: Charging pause           (uint16, 0=run, 1=pause)
  196: Energy limit             (uint32, Wh, 0=unlimited)
  198: Phase count max          (uint16, 1-3)

Energy & Status (Read-Only):
  199: Total lifetime energy    (uint64, Wh)
  203: Session energy           (uint32, Wh)
  205: Average voltage          (uint16, factor 100)
  206: Max signaled current     (uint16, factor 10)
  210: Combined active power    (int32, factor 1000, W)
  217-219: L1/L2/L3 voltage     (uint16, factor 100)
  220-222: L1/L2/L3 current     (uint16, factor 1000)
  224-228: L1/L2/L3 active power (int32, factor 1000, W)
  251: Charging status          (uint16, 0-7)
  252: Charge permission        (uint16)
"""

import logging
from api.charger import Charger
from api.meter import Meter, MeterEnergy
from api.interfaces import PhaseCurrents
from drivers import register
from drivers.modbus.connection import get_connection, ModbusConnection

log = logging.getLogger("ems.drivers.nrgkick")

# Status-Mapping (Register 251 — Charging Status)
# Basierend auf IEC 61851 States
STATUS_MAP = {
    0: "A",   # A1 — nicht verbunden, keine Spannung
    1: "A",   # A2 — nicht verbunden, Spannung vorhanden
    2: "B",   # B1 — verbunden, nicht laden, keine Spannung
    3: "B",   # B2 — verbunden, nicht laden, Spannung vorhanden
    4: "C",   # C1 — laden aktiv
    5: "C",   # C2 — laden aktiv, Belüftung angefordert
    6: "F",   # Fehler
    7: "F",   # Unbekannt
}


@register("nrgkick_modbus")
class NRGKickCharger(Charger, Meter, PhaseCurrents):
    """NRG Kick Connect / PRO Wallbox über Modbus TCP (Gen2 API).

    Nutzt die offiziellen Register-Adressen der NRG Kick Gen2 Local API.
    """

    MIN_CURRENT = 6.0   # Ampere (IEC 61851)
    MAX_CURRENT = 32.0  # Ampere

    def __init__(self, config: dict):
        conn_params = config.get("connection_params") or {}
        self.host = conn_params.get("host", "")
        self.port = int(conn_params.get("port", 502))
        self.unit_id = int(conn_params.get("unit_id", 1))
        self.timeout = int(conn_params.get("timeout_ms", 3000)) / 1000
        self.name = config.get("name", "NRG Kick")
        self.register_map: dict = config.get("modbus_register_map") or {}

        self._conn: ModbusConnection | None = None
        self._cache: dict[str, float] = {}
        self._enabled = True  # NRG Kick ist standardmäßig aktiv
        self._last_status = "A"

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
            word_order=reg.get("word_order", "lsw"),  # NRG Kick = LSW-first
        )
        return val if val is not None else 0.0

    def _write_reg(self, key: str, value: float) -> bool:
        """Schreibt einen skalierten Wert in ein writable Register."""
        reg = self.register_map.get(key)
        if not reg or not reg.get("writable"):
            log.error("NRG Kick: Register %s nicht writable", key)
            return False
        scale = float(reg.get("scale", 1))
        raw = int(value / scale) if scale != 0 else int(value)
        ok = self._get_conn().write_register(reg["address"], raw, self.unit_id)
        log.info("NRG Kick WRITE: %s addr=%d raw=%d (value=%.1f scale=%.3f) → %s",
                 key, reg["address"], raw, value, scale, "OK" if ok else "FAIL")
        return ok

    # ── Charger Interface ─────────────────────────────────────────────────────

    def status(self) -> str:
        raw = int(self._read_reg("charging_state"))
        s = STATUS_MAP.get(raw, "F")
        self._cache["charging_state"] = raw
        self._last_status = s
        log.debug("NRG Kick status: raw=%d → %s", raw, s)
        return s

    def enabled(self) -> bool:
        return self._enabled

    def enable(self, on: bool) -> None:
        # Register 195: Pause State (0=run, 1=pause) — invertiert!
        if "charging_pause" in self.register_map:
            pause_val = 0.0 if on else 1.0  # on=True → pause=0 (run)
            # Read-before-write
            before = self._read_reg("charging_pause")
            ok = self._write_reg("charging_pause", pause_val)
            # Read-after-write
            after = self._read_reg("charging_pause")
            log.info("NRG Kick %s enable(%s): pause_reg before=%.0f after=%.0f write=%s",
                     self.name, on, before, after, "OK" if ok else "FAIL")
        elif "max_current_setpoint" in self.register_map:
            if not on:
                ok = self._write_reg("max_current_setpoint", 0)
            else:
                ok = True
        else:
            ok = True
        if ok:
            self._enabled = on

    def max_current(self, current: float) -> None:
        current = max(self.MIN_CURRENT, min(self.MAX_CURRENT, current))
        if "max_current_setpoint" in self.register_map:
            ok = self._write_reg("max_current_setpoint", current)
            if ok:
                log.info("NRG Kick %s: Ladestrom → %.1fA", self.name, current)
                self._cache["max_current_setpoint"] = current
            else:
                log.error("NRG Kick %s: Strom setzen fehlgeschlagen", self.name)
        else:
            log.warning("NRG Kick %s: Kein max_current_setpoint Register", self.name)

    # ── Meter Interface ───────────────────────────────────────────────────────

    def current_power(self) -> float:
        for key in ("charging_power", "power"):
            if key in self.register_map:
                val = self._read_reg(key)
                self._cache[key] = val
                return val
        return 0.0

    # ── PhaseCurrents Interface ───────────────────────────────────────────────

    def currents(self) -> tuple[float, float, float]:
        l1 = self._read_reg("current_l1") if "current_l1" in self.register_map else 0
        l2 = self._read_reg("current_l2") if "current_l2" in self.register_map else 0
        l3 = self._read_reg("current_l3") if "current_l3" in self.register_map else 0
        self._cache["current_l1"] = l1
        self._cache["current_l2"] = l2
        self._cache["current_l3"] = l3
        return (l1, l2, l3)

    # ── Telemetrie ────────────────────────────────────────────────────────────

    def poll_all(self) -> dict[str, float]:
        """Pollt alle Register aus der DB-Config."""
        for key, reg in self.register_map.items():
            val = self._read_reg(key)
            metric_key = reg.get("metric_key", key)
            self._cache[metric_key] = val
        return dict(self._cache)

    def get_telemetry_metrics(self) -> list[dict]:
        data = self.poll_all()
        result = []
        for key, reg in self.register_map.items():
            metric_key = reg.get("metric_key", key)
            if metric_key in data:
                result.append({
                    "metric_type": metric_key,
                    "value": data[metric_key],
                    "unit": reg.get("unit", ""),
                })
        return result

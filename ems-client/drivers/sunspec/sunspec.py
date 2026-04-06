"""SunSpec Protocol Layer — Ein Driver für ALLE SunSpec-kompatiblen Geräte.

SunSpec (IEEE 1547) ist ein Standard für Modbus-Register-Layouts.
Ein einziger Driver bedient: Fronius Gen24, SMA STP, SolarEdge, Huawei,
Kostal, GoodWe, Deye, und alle anderen SunSpec-kompatiblen Wechselrichter.

Referenz: https://sunspec.org/sunspec-modbus-specifications/
evcc-Referenz: meter/sunspec.go, plugin/sunspec.go
"""

import logging
import struct
from dataclasses import dataclass, field
from drivers.modbus.connection import get_connection, ModbusConnection
from drivers import register
from api.meter import Meter, MeterEnergy
from api.battery import Battery
from api.interfaces import PhasePowers

log = logging.getLogger("ems.sunspec")

# ── SunSpec Constants ────────────────────────────────────────────────────────

SUNSPEC_MAGIC = 0x53756E53  # "SunS" als uint32
SUNSPEC_BASE_ADDRS = [40000, 0, 50000]  # Mögliche Startadressen

# SunSpec Model IDs
MODEL_COMMON        = 1
MODEL_INVERTER_1P   = 101
MODEL_INVERTER_SP   = 102   # Split-phase
MODEL_INVERTER_3P   = 103
MODEL_NAMEPLATE     = 120
MODEL_SETTINGS      = 121
MODEL_STATUS        = 122
MODEL_CONTROLS      = 123
MODEL_STORAGE       = 124
MODEL_MPPT          = 160
MODEL_METER_1P      = 201
MODEL_METER_SP      = 202
MODEL_METER_3P_WYE  = 203
MODEL_METER_3P_DELTA = 204
MODEL_END           = 0xFFFF


@dataclass
class SunSpecModel:
    """Ein erkanntes SunSpec Model mit Adresse und Länge."""
    model_id: int
    address: int     # Startadresse der Model-Daten (nach Header)
    length: int      # Anzahl Register im Model


@dataclass
class SunSpecDevice:
    """Erkanntes SunSpec-Gerät mit allen Models."""
    base_address: int
    manufacturer: str = ""
    model_name: str = ""
    serial: str = ""
    models: list[SunSpecModel] = field(default_factory=list)

    def get_model(self, model_id: int) -> SunSpecModel | None:
        """Findet ein Model anhand der ID."""
        for m in self.models:
            if m.model_id == model_id:
                return m
        return None

    def has_model(self, model_id: int) -> bool:
        return self.get_model(model_id) is not None


# ── SunSpec Scanner ──────────────────────────────────────────────────────────

def scan_device(conn: ModbusConnection, unit_id: int = 1) -> SunSpecDevice | None:
    """Scannt ein Modbus-Gerät nach SunSpec Models.

    Liest den SunSpec-Header, dann iteriert über alle Model-Blöcke
    bis zum End-Marker (0xFFFF).
    """
    for base in SUNSPEC_BASE_ADDRS:
        # Prüfe SunSpec Magic "SunS"
        hi = conn.read_register(base, "uint16", unit_id=unit_id)
        lo = conn.read_register(base + 1, "uint16", unit_id=unit_id)
        if hi is None or lo is None:
            continue
        magic = (int(hi) << 16) | int(lo)
        if magic != SUNSPEC_MAGIC:
            continue

        log.info("SunSpec gefunden bei Adresse %d, unit %d", base, unit_id)
        device = SunSpecDevice(base_address=base)

        # Model 1 (Common) lesen für Hersteller-Info
        addr = base + 2  # Nach "SunS"
        model_id_raw = conn.read_register(addr, "uint16", unit_id=unit_id)
        model_len_raw = conn.read_register(addr + 1, "uint16", unit_id=unit_id)

        if model_id_raw is not None and int(model_id_raw) == MODEL_COMMON:
            length = int(model_len_raw) if model_len_raw else 65
            device.models.append(SunSpecModel(MODEL_COMMON, addr + 2, length))
            # Herstellername aus Model 1 (Register 2-17 = 16 Register = 32 Chars)
            device.manufacturer = _read_string(conn, addr + 2, 16, unit_id)
            device.model_name = _read_string(conn, addr + 18, 16, unit_id)
            device.serial = _read_string(conn, addr + 50, 16, unit_id)
            log.info("SunSpec Gerät: %s %s (SN: %s)",
                     device.manufacturer, device.model_name, device.serial)
            addr += 2 + length
        else:
            addr += 2

        # Weitere Models scannen
        max_scan = 50  # Sicherheitslimit
        for _ in range(max_scan):
            mid = conn.read_register(addr, "uint16", unit_id=unit_id)
            mlen = conn.read_register(addr + 1, "uint16", unit_id=unit_id)

            if mid is None or mlen is None:
                break

            mid_int = int(mid)
            mlen_int = int(mlen)

            if mid_int == MODEL_END or mid_int == 0:
                break

            device.models.append(SunSpecModel(mid_int, addr + 2, mlen_int))
            log.debug("  Model %d: addr=%d len=%d", mid_int, addr + 2, mlen_int)
            addr += 2 + mlen_int

        log.info("SunSpec Scan fertig: %d Models gefunden", len(device.models))
        return device

    return None


def _read_string(conn: ModbusConnection, addr: int, count: int, unit_id: int) -> str:
    """Liest einen SunSpec String (UTF-8, null-padded) aus Registern."""
    chars = []
    for i in range(count):
        val = conn.read_register(addr + i, "uint16", unit_id=unit_id)
        if val is None:
            break
        raw = int(val)
        hi_byte = (raw >> 8) & 0xFF
        lo_byte = raw & 0xFF
        if hi_byte == 0:
            break
        chars.append(chr(hi_byte))
        if lo_byte == 0:
            break
        chars.append(chr(lo_byte))
    return "".join(chars).strip("\x00 ")


# ── SunSpec Inverter Driver ─────────────────────────────────────────────────

@register("sunspec_inverter")
class SunSpecInverter(Meter, MeterEnergy, PhasePowers):
    """Generischer SunSpec Wechselrichter-Treiber.

    Unterstützt: Fronius Gen24, SMA STP SE, SolarEdge, Huawei, Kostal,
    GoodWe, Deye, und alle SunSpec-kompatiblen Geräte.
    """

    def __init__(self, config: dict):
        cp = config.get("connection_params", {})
        self.host = cp.get("host", "")
        self.port = cp.get("port", 502)
        self.unit_id = cp.get("unit_id", 1)
        self.conn = get_connection(self.host, self.port, self.unit_id)
        self._device: SunSpecDevice | None = None
        self._inverter_model: SunSpecModel | None = None
        self._meter_model: SunSpecModel | None = None
        self._scan()

    def _scan(self):
        """SunSpec Auto-Discovery beim Start."""
        self._device = scan_device(self.conn, self.unit_id)
        if not self._device:
            log.warning("Kein SunSpec-Gerät gefunden bei %s:%d unit %d",
                        self.host, self.port, self.unit_id)
            return

        # Inverter Model finden (101, 102 oder 103)
        for mid in (MODEL_INVERTER_3P, MODEL_INVERTER_SP, MODEL_INVERTER_1P):
            m = self._device.get_model(mid)
            if m:
                self._inverter_model = m
                log.info("Inverter Model %d gefunden", mid)
                break

        # Meter Model finden (203, 204, 201, 202)
        for mid in (MODEL_METER_3P_WYE, MODEL_METER_3P_DELTA,
                    MODEL_METER_1P, MODEL_METER_SP):
            m = self._device.get_model(mid)
            if m:
                self._meter_model = m
                log.info("Meter Model %d gefunden", mid)
                break

    def current_power(self) -> float:
        """AC-Leistung in Watt (SunSpec Inverter Model 101-103).

        Register-Offsets im Inverter Model:
          Offset 14: W (AC Power, int16)
          Offset 15: W_SF (Scale Factor, int16)
        """
        if not self._inverter_model:
            return 0.0

        base = self._inverter_model.address
        power_raw = self.conn.read_register(base + 14, "int16", unit_id=self.unit_id)
        sf_raw = self.conn.read_register(base + 15, "int16", unit_id=self.unit_id)

        if power_raw is None:
            return 0.0

        sf = int(sf_raw) if sf_raw is not None else 0
        return float(int(power_raw)) * (10 ** sf)

    def total_energy(self) -> float:
        """Gesamtenergie in kWh (SunSpec Inverter Model).

        Register-Offsets:
          Offset 24: WH (Total Energy, uint32)
          Offset 26: WH_SF (Scale Factor, int16)
        """
        if not self._inverter_model:
            return 0.0

        base = self._inverter_model.address
        energy_raw = self.conn.read_register(base + 24, "uint32", unit_id=self.unit_id)
        sf_raw = self.conn.read_register(base + 26, "int16", unit_id=self.unit_id)

        if energy_raw is None:
            return 0.0

        sf = int(sf_raw) if sf_raw is not None else 0
        wh = float(energy_raw) * (10 ** sf)
        return wh / 1000.0  # Wh → kWh

    def powers(self) -> tuple[float, float, float]:
        """Phasen-Leistungen L1/L2/L3 in Watt.

        Inverter Model 103 (3-Phase):
          Offset 14: W (Total, int16)
          Offset 15: W_SF
          Offsets 2,4,6: PhVphA/B/C (Phase Voltages)
          Offsets 8,9,10: A_phA/B/C (Phase Currents)

        Falls kein 3P-Model: Gesamtleistung auf L1.
        """
        if not self._inverter_model:
            return (0.0, 0.0, 0.0)

        base = self._inverter_model.address

        # Scale Factors
        a_sf = self.conn.read_register(base + 6, "int16", unit_id=self.unit_id)
        v_sf = self.conn.read_register(base + 13, "int16", unit_id=self.unit_id)

        a_scale = 10 ** (int(a_sf) if a_sf is not None else 0)
        v_scale = 10 ** (int(v_sf) if v_sf is not None else 0)

        if self._inverter_model.model_id == MODEL_INVERTER_3P:
            # 3-Phasen: Leistung = V × A pro Phase
            powers_out = []
            for phase_offset_v, phase_offset_a in [(2, 7), (3, 8), (4, 9)]:
                v = self.conn.read_register(base + phase_offset_v, "uint16", unit_id=self.unit_id)
                a = self.conn.read_register(base + phase_offset_a, "uint16", unit_id=self.unit_id)
                if v is not None and a is not None:
                    powers_out.append(float(int(v)) * v_scale * float(int(a)) * a_scale)
                else:
                    powers_out.append(0.0)
            return (powers_out[0], powers_out[1], powers_out[2])

        # Einphasig: Gesamtleistung auf L1
        total = self.current_power()
        return (total, 0.0, 0.0)

    def poll_all(self) -> list[dict]:
        """Pollt alle verfügbaren Messwerte für Telemetrie."""
        metrics = []

        power = self.current_power()
        metrics.append({"metric_type": "pv_power", "value": power, "unit": "W"})

        energy = self.total_energy()
        if energy > 0:
            metrics.append({"metric_type": "pv_energy_total", "value": energy, "unit": "kWh"})

        l1, l2, l3 = self.powers()
        if l1 != 0 or l2 != 0 or l3 != 0:
            metrics.append({"metric_type": "pv_power_l1", "value": l1, "unit": "W"})
            metrics.append({"metric_type": "pv_power_l2", "value": l2, "unit": "W"})
            metrics.append({"metric_type": "pv_power_l3", "value": l3, "unit": "W"})

        # Frequenz (Offset 16 im Inverter Model)
        if self._inverter_model:
            hz = self.conn.read_register(
                self._inverter_model.address + 16, "uint16", unit_id=self.unit_id)
            hz_sf = self.conn.read_register(
                self._inverter_model.address + 17, "int16", unit_id=self.unit_id)
            if hz is not None:
                sf = int(hz_sf) if hz_sf is not None else 0
                metrics.append({
                    "metric_type": "grid_frequency",
                    "value": round(float(int(hz)) * (10 ** sf), 2),
                    "unit": "Hz",
                })

        if self._device:
            metrics.append({
                "metric_type": "sunspec_manufacturer",
                "value": 0,
                "unit": self._device.manufacturer,
            })

        return metrics


# ── SunSpec Meter Driver ─────────────────────────────────────────────────────

@register("sunspec_meter")
class SunSpecMeter(Meter, MeterEnergy, PhasePowers):
    """Generischer SunSpec Meter-Treiber (Model 201-204).

    Für externe Zähler (Grid Meter, PV Meter) die über SunSpec sprechen.
    Z.B. Fronius Smart Meter, SMA Energy Meter, Carlo Gavazzi.
    """

    def __init__(self, config: dict):
        cp = config.get("connection_params", {})
        self.host = cp.get("host", "")
        self.port = cp.get("port", 502)
        self.unit_id = cp.get("unit_id", 1)
        self.conn = get_connection(self.host, self.port, self.unit_id)
        self._device: SunSpecDevice | None = None
        self._meter_model: SunSpecModel | None = None
        self._scan()

    def _scan(self):
        self._device = scan_device(self.conn, self.unit_id)
        if not self._device:
            log.warning("Kein SunSpec Meter bei %s:%d unit %d",
                        self.host, self.port, self.unit_id)
            return

        for mid in (MODEL_METER_3P_WYE, MODEL_METER_3P_DELTA,
                    MODEL_METER_1P, MODEL_METER_SP):
            m = self._device.get_model(mid)
            if m:
                self._meter_model = m
                log.info("Meter Model %d gefunden", mid)
                break

    def current_power(self) -> float:
        """Aktuelle Wirkleistung in Watt.

        Meter Model 201-204:
          Offset 0: W (Total Real Power, int16)
          Offset 1: W_SF (Scale Factor)
        """
        if not self._meter_model:
            return 0.0

        base = self._meter_model.address
        w = self.conn.read_register(base, "int16", unit_id=self.unit_id)
        sf = self.conn.read_register(base + 1, "int16", unit_id=self.unit_id)

        if w is None:
            return 0.0

        scale = 10 ** (int(sf) if sf is not None else 0)
        return float(int(w)) * scale

    def total_energy(self) -> float:
        """Gesamtenergie in kWh.

        Meter Model:
          Offset 36: TotWhExp (Total Exported Energy, uint32)
          Offset 38: TotWhImp (Total Imported Energy, uint32)
          Offset 40: TotWh_SF
        """
        if not self._meter_model:
            return 0.0

        base = self._meter_model.address
        imp = self.conn.read_register(base + 38, "uint32", unit_id=self.unit_id)
        sf = self.conn.read_register(base + 40, "int16", unit_id=self.unit_id)

        if imp is None:
            return 0.0

        scale = 10 ** (int(sf) if sf is not None else 0)
        return float(imp) * scale / 1000.0  # Wh → kWh

    def powers(self) -> tuple[float, float, float]:
        """Phasen-Leistungen L1/L2/L3.

        3P Wye Model (203):
          Offset 2: WphA (L1 Power, int16)
          Offset 3: WphB (L2 Power, int16)
          Offset 4: WphC (L3 Power, int16)
          Offset 1: W_SF
        """
        if not self._meter_model:
            return (0.0, 0.0, 0.0)

        base = self._meter_model.address
        sf_raw = self.conn.read_register(base + 1, "int16", unit_id=self.unit_id)
        scale = 10 ** (int(sf_raw) if sf_raw is not None else 0)

        phases = []
        for offset in (2, 3, 4):
            val = self.conn.read_register(base + offset, "int16", unit_id=self.unit_id)
            phases.append(float(int(val)) * scale if val is not None else 0.0)

        return (phases[0], phases[1], phases[2])

    def poll_all(self) -> list[dict]:
        metrics = []
        power = self.current_power()
        metrics.append({"metric_type": "power", "value": power, "unit": "W"})

        l1, l2, l3 = self.powers()
        metrics.append({"metric_type": "power_l1", "value": l1, "unit": "W"})
        metrics.append({"metric_type": "power_l2", "value": l2, "unit": "W"})
        metrics.append({"metric_type": "power_l3", "value": l3, "unit": "W"})

        energy = self.total_energy()
        if energy > 0:
            metrics.append({"metric_type": "energy_total", "value": energy, "unit": "kWh"})

        return metrics

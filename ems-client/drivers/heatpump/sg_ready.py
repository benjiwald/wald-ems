"""Wärmepumpen-Treiber — SG-Ready Steuerung über Modbus TCP.

SG-Ready (Smart Grid Ready) ist ein Standard für die Ansteuerung
von Wärmepumpen durch Energiemanagementsysteme.

SG-Ready Modi:
  1 = Sperre (EVU-Sperre, WP aus)
  2 = Normal (Standardbetrieb)
  3 = Verstärkt (erhöhte Sollwerte, PV-Überschuss nutzen)
  4 = Erzwungen (maximale Heizleistung, z.B. Power-to-Heat)

Unterstützte Hersteller:
  - KNV / NIBE (S1155, S1255, F1255, VVM310, ...)
  - Heliotherm (HP08-HP40 Serie)
  - Stiebel Eltron / Tecalor (über ISG web)
  - Vaillant / Buderus (über SG-Ready Kontakte)

Konfigurationsbeispiel NIBE (connection_params):
{
    "host": "192.168.1.150",
    "port": 502,
    "unit_id": 1,
    "manufacturer": "nibe",
    "registers": {
        "sg_ready_mode":    {"address": 47206, "type": "uint16", "writable": true},
        "outdoor_temp":     {"address": 40004, "type": "int16", "scale": 0.1},
        "supply_temp":      {"address": 40008, "type": "int16", "scale": 0.1},
        "return_temp":      {"address": 40012, "type": "int16", "scale": 0.1},
        "hot_water_temp":   {"address": 40014, "type": "int16", "scale": 0.1},
        "compressor_freq":  {"address": 43136, "type": "uint16"},
        "current_power":    {"address": 43084, "type": "uint16", "scale": 10},
        "energy_heating":   {"address": 44300, "type": "uint32", "scale": 0.1},
        "energy_hot_water": {"address": 44306, "type": "uint32", "scale": 0.1},
        "cop":              {"address": 44302, "type": "uint16", "scale": 0.1}
    }
}

Konfigurationsbeispiel Heliotherm (connection_params):
{
    "host": "192.168.1.151",
    "port": 502,
    "unit_id": 1,
    "manufacturer": "heliotherm",
    "registers": {
        "sg_ready_mode":    {"address": 4000, "type": "uint16", "writable": true},
        "outdoor_temp":     {"address": 10,   "type": "int16", "scale": 0.1},
        "supply_temp":      {"address": 11,   "type": "int16", "scale": 0.1},
        "return_temp":      {"address": 12,   "type": "int16", "scale": 0.1},
        "hot_water_temp":   {"address": 13,   "type": "int16", "scale": 0.1},
        "current_power":    {"address": 20,   "type": "uint16", "scale": 1},
        "compressor_state": {"address": 100,  "type": "uint16"}
    }
}
"""

import logging
from drivers import register
from drivers.modbus.connection import get_connection
from api.charger import Charger
from api.meter import Meter

log = logging.getLogger("ems.heatpump")

# SG-Ready Modi
SG_BLOCK    = 1  # EVU-Sperre — WP aus
SG_NORMAL   = 2  # Normalbetrieb
SG_BOOST    = 3  # Verstärkt — PV-Überschuss nutzen
SG_FORCE    = 4  # Erzwungen — maximale Leistung


@register("nibe_modbus")
class NIBEHeatPump(Charger, Meter):
    """KNV/NIBE Wärmepumpe über Modbus TCP (NIBE Modbus 40).

    Implementiert das Charger-Interface für SG-Ready Steuerung:
    - enable(True)  → SG_BOOST (Modus 3, PV-Überschuss)
    - enable(False) → SG_NORMAL (Modus 2, Standardbetrieb)
    - max_current() → SG_FORCE bei hohem Überschuss, sonst SG_BOOST

    Unterstützt NIBE S1155, S1255, F1255, VVM310, VVM320, VVM500.
    """

    def __init__(self, config: dict):
        cp = config.get("connection_params", {})
        self.host = cp.get("host", "")
        self.port = cp.get("port", 502)
        self.unit_id = cp.get("unit_id", 1)
        self.regs = cp.get("registers", {})
        self.conn = get_connection(self.host, self.port, self.unit_id)
        self._sg_mode = SG_NORMAL
        self._enabled = False

    def _read_reg(self, key: str) -> float | None:
        reg = self.regs.get(key)
        if not reg:
            return None
        return self.conn.read_register(
            reg["address"],
            reg.get("type", "uint16"),
            reg.get("scale", 1.0),
            unit_id=self.unit_id,
        )

    def _write_sg_mode(self, mode: int) -> bool:
        reg = self.regs.get("sg_ready_mode")
        if not reg or not reg.get("writable"):
            log.warning("SG-Ready Register nicht beschreibbar")
            return False
        ok = self.conn.write_register(reg["address"], mode, unit_id=self.unit_id)
        if ok:
            self._sg_mode = mode
            log.info("SG-Ready Modus → %d", mode)
        return ok

    # ── Charger Interface (SG-Ready) ─────────────────────────────────────────

    def status(self) -> str:
        """A=aus, B=bereit, C=aktiv (Kompressor läuft)."""
        power = self._read_reg("current_power")
        if power is not None and power > 100:
            return "C"  # Kompressor aktiv
        return "B"  # Bereit

    def enabled(self) -> bool:
        return self._enabled

    def enable(self, on: bool) -> None:
        if on:
            self._write_sg_mode(SG_BOOST)
            self._enabled = True
        else:
            self._write_sg_mode(SG_NORMAL)
            self._enabled = False

    def max_current(self, current: float) -> None:
        """Bei hohem Überschuss (>3kW ~= >13A) → SG_FORCE, sonst SG_BOOST."""
        if current >= 13:
            self._write_sg_mode(SG_FORCE)
        elif current >= 6:
            self._write_sg_mode(SG_BOOST)
        else:
            self._write_sg_mode(SG_NORMAL)

    # ── Meter Interface ──────────────────────────────────────────────────────

    def current_power(self) -> float:
        val = self._read_reg("current_power")
        return val if val is not None else 0.0

    # ── Polling ──────────────────────────────────────────────────────────────

    def poll_all(self) -> list[dict]:
        metrics = []
        for key in self.regs:
            if key == "sg_ready_mode":
                metrics.append({"metric_type": "sg_ready_mode", "value": self._sg_mode, "unit": ""})
                continue
            val = self._read_reg(key)
            if val is not None:
                unit_map = {
                    "outdoor_temp": "°C", "supply_temp": "°C",
                    "return_temp": "°C", "hot_water_temp": "°C",
                    "current_power": "W", "compressor_freq": "Hz",
                    "energy_heating": "kWh", "energy_hot_water": "kWh",
                    "cop": "",
                }
                metrics.append({
                    "metric_type": key,
                    "value": val,
                    "unit": unit_map.get(key, ""),
                })
        return metrics


@register("heliotherm_modbus")
class HeliothermHeatPump(NIBEHeatPump):
    """Heliotherm Wärmepumpe — nutzt gleiche SG-Ready Logik wie NIBE.

    Unterschied: Andere Modbus-Register (in connection_params definiert).
    Die Register-Adressen kommen aus der DB-Konfiguration.
    """
    pass  # Gleiche Logik, andere Register-Adressen aus DB

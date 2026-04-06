"""Modbus TCP Connection Wrapper mit Reconnect und Pooling."""

import logging
from pymodbus.client import ModbusTcpClient

log = logging.getLogger("ems.modbus")


class ModbusConnection:
    """Wrapper um ModbusTcpClient mit automatischem Reconnect."""

    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self._client: ModbusTcpClient | None = None

    def _ensure_connected(self) -> ModbusTcpClient:
        if self._client is None or not self._client.connected:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = ModbusTcpClient(self.host, port=self.port, timeout=self.timeout)
            if not self._client.connect():
                raise ConnectionError(f"Modbus Verbindung fehlgeschlagen: {self.host}:{self.port}")
            log.debug("Modbus verbunden: %s:%d unit=%d", self.host, self.port, self.unit_id)
        return self._client

    def read_register(self, address: int, reg_type: str = "uint16",
                      scale: float = 1.0, unit_id: int | None = None,
                      func: str = "holding",
                      word_order: str = "msw") -> float | None:
        """Liest ein Register und wendet Scale an. Gibt None bei Fehler zurück.
        func: 'holding' (0x03) oder 'input' (0x04)
        word_order: 'msw' (Standard Modbus) oder 'lsw' (Low-Word-First, z.B. NRG Kick)"""
        uid = unit_id if unit_id is not None else self.unit_id
        try:
            client = self._ensure_connected()
            count = 4 if reg_type == "uint64" else (2 if reg_type in ("int32", "uint32", "float32") else 1)
            if func == "input":
                result = client.read_input_registers(address, count=count, device_id=uid)
            else:
                result = client.read_holding_registers(address, count=count, device_id=uid)

            if result.isError():
                log.debug("Register %d@unit%d: Fehler", address, uid)
                return None

            regs = result.registers

            if reg_type == "int16":
                raw = regs[0] if regs[0] < 32768 else regs[0] - 65536
            elif reg_type == "uint16":
                raw = regs[0]
            elif reg_type == "int32":
                if word_order == "lsw":
                    raw = regs[0] | (regs[1] << 16)
                else:
                    raw = (regs[0] << 16) | regs[1]
                if raw >= 2**31:
                    raw -= 2**32
            elif reg_type == "uint32":
                if word_order == "lsw":
                    raw = regs[0] | (regs[1] << 16)
                else:
                    raw = (regs[0] << 16) | regs[1]
            elif reg_type == "uint64":
                if word_order == "lsw":
                    raw = regs[0] | (regs[1] << 16) | (regs[2] << 32) | (regs[3] << 48)
                else:
                    raw = (regs[0] << 48) | (regs[1] << 32) | (regs[2] << 16) | regs[3]
            elif reg_type == "float32":
                import struct
                if word_order == "lsw":
                    raw = struct.unpack(">f", struct.pack(">HH", regs[1], regs[0]))[0]
                else:
                    raw = struct.unpack(">f", struct.pack(">HH", regs[0], regs[1]))[0]
            else:
                raw = regs[0]

            return round(raw * scale, 4)

        except ConnectionError:
            self._client = None
            return None
        except Exception as e:
            log.debug("Register %d@unit%d lesen fehlgeschlagen: %s", address, uid, e)
            return None

    def write_register(self, address: int, value: int, unit_id: int | None = None) -> bool:
        """Schreibt einen Wert in ein Holding Register."""
        uid = unit_id if unit_id is not None else self.unit_id
        try:
            client = self._ensure_connected()
            result = client.write_register(address, value, device_id=uid)
            if result.isError():
                log.error("Register %d@unit%d schreiben fehlgeschlagen", address, uid)
                return False
            log.debug("Register %d@unit%d = %d geschrieben", address, uid, value)
            return True
        except Exception as e:
            log.error("Register %d@unit%d Write-Fehler: %s", address, uid, e)
            return False

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


# ── Connection Pool ───────────────────────────────────────────────────────────

_pool: dict[str, ModbusConnection] = {}


def get_connection(host: str, port: int = 502, unit_id: int = 1,
                   timeout: float = 3.0) -> ModbusConnection:
    """Gibt eine gecachte oder neue ModbusConnection zurück."""
    key = f"{host}:{port}"
    if key not in _pool:
        _pool[key] = ModbusConnection(host, port, unit_id, timeout)
    conn = _pool[key]
    conn.unit_id = unit_id
    return conn


def close_all():
    """Schließt alle Verbindungen im Pool."""
    for conn in _pool.values():
        conn.close()
    _pool.clear()

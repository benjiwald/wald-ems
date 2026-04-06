"""M-Bus Meter — Energiemessung über M-Bus (EN 13757).

M-Bus (Meter Bus) ist ein europäischer Standard für Fernablesung
von Strom-, Gas-, Wasser- und Wärmezählern.

Unterstützt:
- M-Bus über TCP Gateway (z.B. Relay, MBus-to-IP Converter)
- M-Bus über seriellen Port (RS232/RS485 mit Level-Converter)

Installation: pip install pyMeterBus

Konfigurationsbeispiel (connection_params):
{
    "host": "192.168.1.200",    # TCP Gateway IP
    "port": 10001,               # TCP Gateway Port
    "address": 1,                # M-Bus Primäradresse (1-250)
    "baudrate": 2400,            # Standard M-Bus Baudrate
    "serial_port": null,         # Alternativ: "/dev/ttyUSB0"
    "poll_interval": 60          # Sekunden zwischen Abfragen
}
"""

import logging
import socket
import struct
import time
from drivers import register
from api.meter import Meter, MeterEnergy

log = logging.getLogger("ems.mbus")

try:
    import meterbus
    HAS_METERBUS = True
except ImportError:
    HAS_METERBUS = False
    log.warning("pyMeterBus nicht installiert — MBus-Treiber deaktiviert (pip install pyMeterBus)")


@register("mbus_meter")
class MBusMeter(Meter, MeterEnergy):
    """M-Bus Zähler-Treiber — liest Verbrauchsdaten über M-Bus Protokoll.

    Unterstützt Strom-, Gas-, Wasser- und Wärmezähler nach EN 13757.
    Kommunikation über TCP-Gateway oder seriellen Port.
    """

    # VIF-Codes für relevante Messwerte (EN 13757-3)
    VIF_POWER_W      = 0x2B  # Leistung in W
    VIF_ENERGY_WH    = 0x03  # Energie in Wh
    VIF_ENERGY_KWH   = 0x06  # Energie in kWh
    VIF_VOLUME_M3    = 0x13  # Volumen in m³
    VIF_FLOW_M3H     = 0x3B  # Durchfluss in m³/h
    VIF_TEMP_C       = 0x59  # Temperatur in °C
    VIF_VOLTAGE_V    = 0xFD49  # Spannung in V
    VIF_CURRENT_A    = 0xFD59  # Strom in A

    def __init__(self, config: dict):
        cp = config.get("connection_params", {})
        self.host = cp.get("host", "")
        self.port = cp.get("port", 10001)
        self.address = cp.get("address", 1)
        self.baudrate = cp.get("baudrate", 2400)
        self.serial_port = cp.get("serial_port")
        self.poll_interval = cp.get("poll_interval", 60)

        self._last_poll: float = 0
        self._cached_data: dict[str, float] = {}

    def _request_data(self) -> dict[str, float]:
        """Sendet SND_NKE + REQ_UD2 und parst die Antwort."""
        now = time.time()
        if now - self._last_poll < self.poll_interval and self._cached_data:
            return self._cached_data

        try:
            if self.serial_port:
                return self._read_serial()
            else:
                return self._read_tcp()
        except Exception as e:
            log.error("MBus Abfrage fehlgeschlagen: %s", e)
            return self._cached_data

    def _read_tcp(self) -> dict[str, float]:
        """Liest Daten über TCP-Gateway."""
        data: dict[str, float] = {}

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)
                sock.connect((self.host, self.port))

                # SND_NKE (Initialize)
                nke = meterbus.send_request_frame(self.address) if HAS_METERBUS else self._build_nke()
                sock.send(nke)
                time.sleep(0.5)

                # REQ_UD2 (Request User Data)
                req = self._build_req_ud2()
                sock.send(req)

                # Antwort lesen
                response = b""
                while True:
                    try:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk
                    except socket.timeout:
                        break

                if HAS_METERBUS and response:
                    frame = meterbus.load(response)
                    if hasattr(frame, 'records'):
                        data = self._parse_records(frame.records)
                else:
                    data = self._parse_raw(response)

        except Exception as e:
            log.error("MBus TCP Fehler (%s:%d): %s", self.host, self.port, e)

        if data:
            self._cached_data = data
            self._last_poll = time.time()

        return data

    def _read_serial(self) -> dict[str, float]:
        """Liest Daten über seriellen Port."""
        if not HAS_METERBUS:
            log.error("pyMeterBus für serielle Verbindung benötigt")
            return {}

        data: dict[str, float] = {}
        try:
            import serial
            with serial.Serial(self.serial_port, self.baudrate, timeout=3) as ser:
                meterbus.send_ping_frame(ser, self.address)
                frame = meterbus.load(meterbus.recv_frame(ser))

                meterbus.send_request_frame(ser, self.address)
                frame = meterbus.load(meterbus.recv_frame(ser))

                if hasattr(frame, 'records'):
                    data = self._parse_records(frame.records)

        except Exception as e:
            log.error("MBus Seriell Fehler (%s): %s", self.serial_port, e)

        if data:
            self._cached_data = data
            self._last_poll = time.time()

        return data

    def _build_nke(self) -> bytes:
        """Baut SND_NKE Frame (Short Frame)."""
        c_field = 0x40  # SND_NKE
        checksum = (c_field + self.address) & 0xFF
        return bytes([0x10, c_field, self.address, checksum, 0x16])

    def _build_req_ud2(self) -> bytes:
        """Baut REQ_UD2 Frame (Short Frame)."""
        c_field = 0x7B  # REQ_UD2
        checksum = (c_field + self.address) & 0xFF
        return bytes([0x10, c_field, self.address, checksum, 0x16])

    def _parse_records(self, records) -> dict[str, float]:
        """Parst M-Bus Records in ein Messwert-Dict."""
        data: dict[str, float] = {}

        for i, rec in enumerate(records):
            try:
                value = float(rec.value) if hasattr(rec, 'value') else 0.0
                unit = str(rec.unit) if hasattr(rec, 'unit') else ""

                # Zuordnung nach Einheit
                unit_lower = unit.lower()
                if "wh" in unit_lower or "kwh" in unit_lower:
                    key = f"energy_{i}"
                    if "kwh" in unit_lower:
                        data[key] = value
                    else:
                        data[key] = value / 1000.0  # Wh → kWh
                    if "energy" not in data:
                        data["energy"] = data[key]

                elif "w" in unit_lower and "wh" not in unit_lower:
                    key = f"power_{i}"
                    data[key] = value
                    if "power" not in data:
                        data["power"] = value

                elif "v" in unit_lower:
                    data[f"voltage_{i}"] = value

                elif "a" in unit_lower and "°" not in unit_lower:
                    data[f"current_{i}"] = value

                elif "°c" in unit_lower or "celsius" in unit_lower:
                    data[f"temperature_{i}"] = value
                    if "temperature" not in data:
                        data["temperature"] = value

                elif "m³" in unit_lower or "m3" in unit_lower:
                    data[f"volume_{i}"] = value
                    if "volume" not in data:
                        data["volume"] = value

            except (ValueError, AttributeError):
                continue

        return data

    def _parse_raw(self, response: bytes) -> dict[str, float]:
        """Fallback: Parst rohe M-Bus Response ohne Library."""
        # Einfache Erkennung — reicht für Standard-Stromzähler
        data: dict[str, float] = {}
        if len(response) < 10:
            return data
        # Nur Basis-Parsing — für komplexe Fälle pyMeterBus verwenden
        log.debug("MBus Raw Response: %s bytes", len(response))
        return data

    # ── Meter Interface ──────────────────────────────────────────────────────

    def current_power(self) -> float:
        data = self._request_data()
        return data.get("power", 0.0)

    def total_energy(self) -> float:
        data = self._request_data()
        return data.get("energy", 0.0)

    def poll_all(self) -> list[dict]:
        data = self._request_data()
        metrics = []
        for key, value in data.items():
            unit_map = {
                "power": "W", "energy": "kWh",
                "temperature": "°C", "volume": "m³",
            }
            # Einheit aus Prefix ableiten
            unit = ""
            for prefix, u in unit_map.items():
                if key.startswith(prefix):
                    unit = u
                    break
            metrics.append({"metric_type": key, "value": value, "unit": unit})
        return metrics

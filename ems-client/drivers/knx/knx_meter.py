"""KNX/IP Meter — Energiemessung über KNX-Bus via IP-Gateway.

Nutzt die xknx Python-Library für KNXnet/IP Tunneling.
Gruppenadressen werden über die DB-Konfiguration (connection_params) definiert.

Installation: pip install xknx

Konfigurationsbeispiel (connection_params):
{
    "host": "192.168.1.100",           # KNX IP Gateway
    "port": 3671,                       # Standard KNXnet/IP Port
    "group_addresses": {
        "power":       "1/2/3",         # Leistung in W (DPT 14.056)
        "power_l1":    "1/2/4",
        "power_l2":    "1/2/5",
        "power_l3":    "1/2/6",
        "voltage_l1":  "1/3/1",         # Spannung in V (DPT 14.027)
        "voltage_l2":  "1/3/2",
        "voltage_l3":  "1/3/3",
        "current_l1":  "1/4/1",         # Strom in A (DPT 14.019)
        "current_l2":  "1/4/2",
        "current_l3":  "1/4/3",
        "energy":      "1/5/1",         # Energie in kWh (DPT 13.013)
        "switch":      "1/1/1",         # Schaltaktor (DPT 1.001) — optional
        "temperature": "1/6/1"          # Temperatur in °C (DPT 9.001)
    }
}
"""

import asyncio
import logging
from drivers import register
from api.meter import Meter, MeterEnergy
from api.interfaces import PhasePowers

log = logging.getLogger("ems.knx")

try:
    from xknx import XKNX
    from xknx.remote_value import RemoteValueSensor
    HAS_XKNX = True
except ImportError:
    HAS_XKNX = False
    log.warning("xknx nicht installiert — KNX-Treiber deaktiviert (pip install xknx)")


@register("knx_meter")
class KNXMeter(Meter, MeterEnergy, PhasePowers):
    """KNX/IP Energiemessung — liest Werte von KNX-Gruppenadressen.

    Unterstützt alle KNX-fähigen Energiezähler, Aktoren und Sensoren
    über ein KNX/IP Gateway (z.B. Weinzierl, ABB, MDT, Hager).
    """

    def __init__(self, config: dict):
        if not HAS_XKNX:
            raise ImportError("pip install xknx erforderlich für KNX-Treiber")

        cp = config.get("connection_params", {})
        self.host = cp.get("host", "")
        self.port = cp.get("port", 3671)
        self.gas = cp.get("group_addresses", {})
        self._values: dict[str, float] = {}
        self._xknx: XKNX | None = None
        self._loop = asyncio.new_event_loop()
        self._connected = False
        self._connect()

    def _connect(self):
        """Verbindet zum KNX/IP Gateway."""
        try:
            self._xknx = XKNX(
                connection_config={
                    "connection_type": "TUNNELING",
                    "gateway_ip": self.host,
                    "gateway_port": self.port,
                }
            )
            self._loop.run_until_complete(self._xknx.start())
            self._connected = True
            log.info("KNX verbunden: %s:%d", self.host, self.port)
        except Exception as e:
            log.error("KNX Verbindung fehlgeschlagen: %s", e)
            self._connected = False

    def _read_ga(self, key: str) -> float:
        """Liest einen Wert von einer Gruppenadresse."""
        ga = self.gas.get(key)
        if not ga or not self._connected or not self._xknx:
            return 0.0

        try:
            from xknx.core import ValueReader
            reader = ValueReader(self._xknx, ga)
            telegram = self._loop.run_until_complete(
                asyncio.wait_for(reader.read(), timeout=3.0)
            )
            if telegram and telegram.payload:
                # DPT-Wert aus Payload extrahieren
                raw = telegram.payload.value
                if isinstance(raw, (int, float)):
                    return float(raw)
            return 0.0
        except Exception as e:
            log.debug("KNX GA %s lesen fehlgeschlagen: %s", ga, e)
            return 0.0

    def _write_ga(self, key: str, value: bool) -> bool:
        """Schreibt einen Bool-Wert auf eine Gruppenadresse (Schaltaktor)."""
        ga = self.gas.get(key)
        if not ga or not self._connected or not self._xknx:
            return False

        try:
            from xknx.devices import Switch
            switch = Switch(self._xknx, "EMS_Switch", group_address=ga)
            self._loop.run_until_complete(switch.set(value))
            return True
        except Exception as e:
            log.error("KNX GA %s schreiben fehlgeschlagen: %s", ga, e)
            return False

    # ── Meter Interface ──────────────────────────────────────────────────────

    def current_power(self) -> float:
        return self._read_ga("power")

    def total_energy(self) -> float:
        return self._read_ga("energy")

    def powers(self) -> tuple[float, float, float]:
        return (
            self._read_ga("power_l1"),
            self._read_ga("power_l2"),
            self._read_ga("power_l3"),
        )

    # ── Polling ──────────────────────────────────────────────────────────────

    def poll_all(self) -> list[dict]:
        """Pollt alle konfigurierten Gruppenadressen."""
        metrics = []
        for key in self.gas:
            if key == "switch":
                continue  # Switch ist kein Messwert
            val = self._read_ga(key)
            unit_map = {
                "power": "W", "power_l1": "W", "power_l2": "W", "power_l3": "W",
                "voltage_l1": "V", "voltage_l2": "V", "voltage_l3": "V",
                "current_l1": "A", "current_l2": "A", "current_l3": "A",
                "energy": "kWh", "temperature": "°C",
            }
            metrics.append({
                "metric_type": key,
                "value": val,
                "unit": unit_map.get(key, ""),
            })
        return metrics

    def close(self):
        if self._xknx and self._connected:
            try:
                self._loop.run_until_complete(self._xknx.stop())
            except Exception:
                pass

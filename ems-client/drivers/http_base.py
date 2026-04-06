"""HTTP REST Base — Wiederverwendbare HTTP-Session für REST-basierte Geräte.

Genutzt von: Shelly, Sigenergy, go-e, Fronius Solar API, etc.
Bietet: Session-Management, JSON-Caching, Auth, Retry.
"""

import logging
import time
from typing import Any

log = logging.getLogger("ems.http")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    log.warning("requests nicht installiert — HTTP-Treiber deaktiviert")


class HTTPSession:
    """HTTP-Session mit Caching und Retry für REST-basierte Geräte."""

    def __init__(self, base_url: str, auth: dict | None = None,
                 timeout: float = 5.0, cache_seconds: float = 2.0):
        """
        Args:
            base_url: Basis-URL (z.B. "http://192.168.1.50")
            auth: Auth-Konfiguration:
                  {"type": "basic", "user": "...", "password": "..."}
                  {"type": "bearer", "token": "..."}
                  {"type": "api_key", "header": "X-API-Key", "key": "..."}
            timeout: Request-Timeout in Sekunden
            cache_seconds: Wie lange gecachte Antworten gültig sind
        """
        if not HAS_REQUESTS:
            raise ImportError("pip install requests erforderlich für HTTP-Treiber")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._session = requests.Session()
        self._cache: dict[str, tuple[float, Any]] = {}

        # Auth konfigurieren
        if auth:
            auth_type = auth.get("type", "")
            if auth_type == "basic":
                self._session.auth = (auth["user"], auth["password"])
            elif auth_type == "bearer":
                self._session.headers["Authorization"] = f"Bearer {auth['token']}"
            elif auth_type == "api_key":
                header = auth.get("header", "X-API-Key")
                self._session.headers[header] = auth["key"]

        self._session.headers.setdefault("Accept", "application/json")

    def get_json(self, path: str, params: dict | None = None,
                 cache: bool = True) -> dict | list | None:
        """GET Request mit JSON-Antwort und optionalem Cache.

        Args:
            path: URL-Pfad (z.B. "/api/v1/status")
            params: Query-Parameter
            cache: Ob die Antwort gecacht werden soll

        Returns:
            Parsed JSON oder None bei Fehler
        """
        cache_key = f"GET:{path}:{params}"

        if cache and cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if time.time() - cached_time < self.cache_seconds:
                return cached_data

        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

            if cache:
                self._cache[cache_key] = (time.time(), data)

            return data

        except requests.Timeout:
            log.warning("HTTP Timeout: %s", url)
            return None
        except requests.ConnectionError:
            log.warning("HTTP Verbindung fehlgeschlagen: %s", url)
            return None
        except Exception as e:
            log.warning("HTTP Fehler bei %s: %s", url, e)
            return None

    def post_json(self, path: str, data: dict | None = None,
                  json_body: dict | None = None) -> dict | None:
        """POST Request mit JSON-Body."""
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.post(
                url, data=data, json=json_body, timeout=self.timeout)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {}
        except Exception as e:
            log.warning("HTTP POST Fehler bei %s: %s", url, e)
            return None

    def put_json(self, path: str, json_body: dict) -> dict | None:
        """PUT Request mit JSON-Body."""
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.put(url, json=json_body, timeout=self.timeout)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {}
        except Exception as e:
            log.warning("HTTP PUT Fehler bei %s: %s", url, e)
            return None

    def get_value(self, path: str, json_path: str,
                  default: float = 0.0) -> float:
        """Holt einen einzelnen Wert aus einer JSON-Antwort.

        Args:
            path: URL-Pfad
            json_path: Dot-separated Pfad im JSON (z.B. "Body.Data.PAC.Value")
            default: Fallback bei Fehler

        Returns:
            Extrahierter float-Wert
        """
        data = self.get_json(path)
        if data is None:
            return default

        try:
            obj = data
            for key in json_path.split("."):
                if isinstance(obj, dict):
                    obj = obj[key]
                elif isinstance(obj, list) and key.isdigit():
                    obj = obj[int(key)]
                else:
                    return default
            return float(obj)
        except (KeyError, IndexError, TypeError, ValueError):
            return default

    def clear_cache(self):
        """Löscht den gesamten Cache."""
        self._cache.clear()

    def close(self):
        """Schließt die HTTP-Session."""
        self._session.close()


# ── Generic HTTP REST Device ─────────────────────────────────────────────────

from drivers import register
from api.meter import Meter, MeterEnergy
from api.charger import Charger


@register("http_rest_device")
class HTTPRestDevice(Meter):
    """Generischer HTTP REST Treiber — konfiguriert über DB.

    Die Geräte-Konfiguration (connection_params) definiert:
    - base_url: "http://192.168.1.50"
    - auth: {"type": "basic", ...} (optional)
    - endpoints: JSON-Mapping von Metriken zu API-Pfaden

    Beispiel endpoints:
    {
        "power": {"path": "/api/v1/status", "json_path": "Body.Data.PAC.Value", "unit": "W"},
        "energy": {"path": "/api/v1/status", "json_path": "Body.Data.TOTAL_ENERGY.Value", "unit": "kWh"},
        "voltage_l1": {"path": "/status", "json_path": "emeters.0.voltage", "unit": "V"}
    }
    """

    def __init__(self, config: dict):
        cp = config.get("connection_params", {})
        host = cp.get("host", "")
        port = cp.get("port", 80)
        protocol = cp.get("protocol", "http")
        base_url = cp.get("base_url", f"{protocol}://{host}:{port}")
        auth = cp.get("auth")
        cache_s = cp.get("cache_seconds", 2.0)

        self.session = HTTPSession(base_url, auth=auth, cache_seconds=cache_s)
        self.endpoints = cp.get("endpoints", {})

    def current_power(self) -> float:
        ep = self.endpoints.get("power", {})
        if not ep:
            return 0.0
        return self.session.get_value(ep["path"], ep["json_path"])

    def poll_all(self) -> list[dict]:
        """Pollt alle konfigurierten Endpoints."""
        metrics = []
        for metric_key, ep in self.endpoints.items():
            if not isinstance(ep, dict) or "path" not in ep:
                continue
            value = self.session.get_value(
                ep["path"], ep["json_path"], default=None)
            if value is not None:
                metrics.append({
                    "metric_type": metric_key,
                    "value": value,
                    "unit": ep.get("unit", ""),
                })
        return metrics

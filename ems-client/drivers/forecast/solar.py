"""Solar Forecast — PV-Prognose via forecast.solar (kostenlos, kein API-Key).

Unterstützt mehrere Dachflächen (Planes) mit unterschiedlicher
Ausrichtung und Neigung. Pro Plane ein API-Call, Ergebnisse werden summiert.

API: https://api.forecast.solar/estimate/:lat/:lon/:dec/:az/:kwp

Konfiguration (in site_configs):
{
    "forecast": {
        "lat": 47.07,
        "lon": 15.44,
        "planes": [
            {"name": "Ost",  "kwp": 5.0, "declination": 10, "azimuth": -90},
            {"name": "West", "kwp": 5.0, "declination": 10, "azimuth": 90},
            {"name": "Süd",  "kwp": 3.0, "declination": 90, "azimuth": 0}
        ]
    }
}

Rate Limit: Max 12 Abfragen/Stunde (free tier). Bei 4 Planes = 3 Polls/Stunde → alle 20 Min OK.
"""

import logging
import time
from datetime import datetime, timedelta

import requests

log = logging.getLogger("ems.forecast.solar")

API_BASE = "https://api.forecast.solar/estimate"


class SolarForecast:
    """Holt PV-Ertragsprognose von forecast.solar für mehrere Dachflächen."""

    def __init__(self, config: dict):
        fc = config.get("forecast", {})
        self.lat = fc.get("lat", 47.07)
        self.lon = fc.get("lon", 15.44)

        # Multi-Plane oder Single-Plane (Fallback)
        planes = fc.get("planes", [])
        if not planes:
            # Fallback: alte Single-Plane Config
            self.planes = [{
                "name": "PV",
                "kwp": fc.get("kwp", 10.0),
                "declination": fc.get("declination", 30),
                "azimuth": fc.get("azimuth", 0),
            }]
        else:
            self.planes = planes

        self.kwp = sum(p.get("kwp", 0) for p in self.planes)

        # Poll-Interval: 12 Calls/h free → bei N planes alle (3600/12*N) Sekunden
        # Minimum 1200s (20 Min), Maximum 3600s (1h)
        self._poll_interval = max(1200, min(3600, len(self.planes) * 300))

        # Aggregierte Ergebnisse
        self._forecast: dict[str, float] = {}  # "2026-04-05 13:00:00" → Watt (Summe)
        self._plane_results: list[dict] = []  # Pro-Plane Ergebnisse für Dashboard
        self._last_poll: float = 0
        self._today_kwh: float = 0
        self._tomorrow_kwh: float = 0
        self._current_estimate_w: float = 0
        self._error: str | None = None

    def poll(self):
        """Holt neue Prognose wenn Interval abgelaufen."""
        now = time.time()
        if now - self._last_poll < self._poll_interval:
            self._update_current_estimate()
            return

        total_forecast: dict[str, float] = {}
        total_today_wh = 0.0
        total_tomorrow_wh = 0.0
        plane_results = []
        errors = []

        today_str = datetime.now().strftime("%Y-%m-%d")
        tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        for plane in self.planes:
            name = plane.get("name", "PV")
            kwp = plane.get("kwp", 0)
            dec = plane.get("declination", 30)
            az = plane.get("azimuth", 0)

            if kwp <= 0:
                continue

            url = f"{API_BASE}/{self.lat}/{self.lon}/{dec}/{az}/{kwp}"

            try:
                resp = requests.get(url, timeout=15, headers={"Accept": "application/json"})
                resp.raise_for_status()
                data = resp.json()

                if data.get("result"):
                    watts = data["result"].get("watts", {})
                    wh_day = data["result"].get("watt_hours_day", {})

                    plane_today = wh_day.get(today_str, 0) / 1000
                    plane_tomorrow = wh_day.get(tomorrow_str, 0) / 1000
                    total_today_wh += plane_today
                    total_tomorrow_wh += plane_tomorrow

                    # Summiere in Gesamt-Forecast
                    for ts_str, w in watts.items():
                        total_forecast[ts_str] = total_forecast.get(ts_str, 0) + float(w)

                    plane_results.append({
                        "name": name,
                        "kwp": kwp,
                        "declination": dec,
                        "azimuth": az,
                        "today_kwh": round(plane_today, 1),
                        "tomorrow_kwh": round(plane_tomorrow, 1),
                    })

                    log.info("Forecast %s (%.1fkWp, %d°/%d°): heute=%.1f kWh morgen=%.1f kWh",
                             name, kwp, dec, az, plane_today, plane_tomorrow)
                else:
                    err_msg = data.get("message", {}).get("text", "?")
                    errors.append(f"{name}: {err_msg}")

                # Rate limit: kurz warten zwischen Calls
                if len(self.planes) > 1:
                    time.sleep(1)

            except Exception as e:
                errors.append(f"{name}: {e}")
                log.warning("Forecast %s fehlgeschlagen: %s", name, e)

        # Ergebnisse übernehmen
        if total_forecast:
            self._forecast = total_forecast
            self._today_kwh = total_today_wh
            self._tomorrow_kwh = total_tomorrow_wh
            self._plane_results = plane_results
            self._update_current_estimate()
            self._last_poll = time.time()
            self._error = "; ".join(errors) if errors else None

            log.info("Forecast Gesamt (%d Flächen, %.1f kWp): heute=%.1f kWh morgen=%.1f kWh aktuell=%.0f W",
                     len(plane_results), self.kwp, self._today_kwh, self._tomorrow_kwh, self._current_estimate_w)
        elif errors:
            self._error = "; ".join(errors)
            log.warning("Forecast komplett fehlgeschlagen: %s", self._error)

    def _update_current_estimate(self):
        """Interpoliert die aktuelle erwartete PV-Leistung."""
        if not self._forecast:
            return

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:00:00")
        next_hour = now.replace(minute=0, second=0) + timedelta(hours=1)
        next_str = next_hour.strftime("%Y-%m-%d %H:00:00")

        current = self._forecast.get(now_str, 0)
        next_val = self._forecast.get(next_str, current)

        minute_fraction = now.minute / 60.0
        self._current_estimate_w = current + (next_val - current) * minute_fraction

    @property
    def current_estimate_w(self) -> float:
        return self._current_estimate_w

    @property
    def today_kwh(self) -> float:
        return self._today_kwh

    @property
    def tomorrow_kwh(self) -> float:
        return self._tomorrow_kwh

    @property
    def remaining_today_kwh(self) -> float:
        if not self._forecast:
            return 0
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        remaining = 0
        for ts_str, watts in self._forecast.items():
            if ts_str.startswith(today_str):
                hour = int(ts_str.split(" ")[1].split(":")[0])
                if hour >= now.hour:
                    remaining += watts / 1000
        return remaining

    def to_dict(self) -> dict:
        return {
            "current_estimate_w": round(self._current_estimate_w),
            "today_kwh": round(self._today_kwh, 1),
            "tomorrow_kwh": round(self._tomorrow_kwh, 1),
            "remaining_today_kwh": round(self.remaining_today_kwh, 1),
            "total_kwp": round(self.kwp, 1),
            "planes": self._plane_results,
            "error": self._error,
            "last_poll": self._last_poll,
        }

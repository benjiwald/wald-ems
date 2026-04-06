"""aWATTar — Dynamische Strompreise für AT und DE (kostenlos, kein API-Key).

API: https://api.awattar.at/v1/marketdata (AT)
     https://api.awattar.de/v1/marketdata (DE)

Liefert stündliche Strompreise (Day-Ahead EPEX Spot) für die nächsten 24h.
Preise kommen ab ~14:00 für den nächsten Tag.

Konfiguration (in site_configs):
{
    "tariff": {
        "provider": "awattar",
        "country": "at",
        "markup_ct": 3.0,
        "cost_limit_ct": 15.0
    }
}

markup_ct: Aufschlag des Energieversorgers auf den Börsenpreis (ct/kWh)
cost_limit_ct: Nur laden wenn Preis unter diesem Limit (Smart Cost)
"""

import logging
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("ems.tariff.awattar")

POLL_INTERVAL = 3600  # 1 Stunde
API_URLS = {
    "at": "https://api.awattar.at/v1/marketdata",
    "de": "https://api.awattar.de/v1/marketdata",
}


class AWATTarTariff:
    """Holt dynamische Strompreise von aWATTar."""

    def __init__(self, config: dict):
        tc = config.get("tariff", {})
        self.country = tc.get("country", "at")
        self.markup_ct = tc.get("markup_ct", 3.0)  # Versorger-Aufschlag ct/kWh
        self.cost_limit_ct = tc.get("cost_limit_ct", 0)  # 0 = kein Limit

        self._prices: list[dict] = []  # [{start, end, price_ct}]
        self._last_poll: float = 0
        self._current_price_ct: float = 0
        self._error: str | None = None

    def poll(self):
        """Holt aktuelle Marktdaten wenn Interval abgelaufen."""
        now = time.time()
        if now - self._last_poll < POLL_INTERVAL:
            self._update_current_price()
            return

        url = API_URLS.get(self.country, API_URLS["at"])

        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            self._prices = []
            for entry in data.get("data", []):
                start_ms = entry["start_timestamp"]
                end_ms = entry["end_timestamp"]
                # Preis kommt in EUR/MWh -> umrechnen in ct/kWh
                price_eur_mwh = entry["marketprice"]
                price_ct_kwh = price_eur_mwh / 10.0 + self.markup_ct

                self._prices.append({
                    "start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
                    "start_ts": start_ms / 1000,
                    "end_ts": end_ms / 1000,
                    "market_ct": round(price_eur_mwh / 10.0, 2),
                    "total_ct": round(price_ct_kwh, 2),
                })

            self._update_current_price()
            self._last_poll = now
            self._error = None

            log.info("aWATTar: %d Preiszonen geladen, aktuell=%.1f ct/kWh (Markt=%.1f + Aufschlag=%.1f)",
                     len(self._prices), self._current_price_ct,
                     self._current_price_ct - self.markup_ct, self.markup_ct)

        except Exception as e:
            self._error = str(e)
            log.warning("aWATTar Abfrage fehlgeschlagen: %s", e)

    def _update_current_price(self):
        """Findet den aktuellen Preis basierend auf der Uhrzeit."""
        now = time.time()
        for p in self._prices:
            if p["start_ts"] <= now < p["end_ts"]:
                self._current_price_ct = p["total_ct"]
                return
        # Fallback: letzter bekannter Preis
        if self._prices:
            self._current_price_ct = self._prices[-1]["total_ct"]

    @property
    def current_price_ct(self) -> float:
        """Aktueller Strompreis in ct/kWh (inkl. Aufschlag)."""
        return self._current_price_ct

    @property
    def is_cheap(self) -> bool:
        """Ob der aktuelle Preis unter dem Cost-Limit liegt."""
        if self.cost_limit_ct <= 0:
            return True  # Kein Limit konfiguriert
        return self._current_price_ct <= self.cost_limit_ct

    def cheapest_hours(self, hours: int = 3, within_hours: int = 24) -> list[dict]:
        """Findet die N günstigsten Stunden in den nächsten within_hours Stunden."""
        now = time.time()
        future = [p for p in self._prices if p["start_ts"] >= now and p["start_ts"] < now + within_hours * 3600]
        future.sort(key=lambda p: p["total_ct"])
        return future[:hours]

    @property
    def min_price_ct(self) -> float:
        """Günstigster Preis in den nächsten Stunden."""
        now = time.time()
        future = [p["total_ct"] for p in self._prices if p["start_ts"] >= now]
        return min(future) if future else self._current_price_ct

    @property
    def max_price_ct(self) -> float:
        """Teuerster Preis in den nächsten Stunden."""
        now = time.time()
        future = [p["total_ct"] for p in self._prices if p["start_ts"] >= now]
        return max(future) if future else self._current_price_ct

    @property
    def avg_price_ct(self) -> float:
        """Durchschnittspreis in den nächsten Stunden."""
        now = time.time()
        future = [p["total_ct"] for p in self._prices if p["start_ts"] >= now]
        return sum(future) / len(future) if future else self._current_price_ct

    def get_prices(self, hours: int = 24) -> list[dict]:
        """Gibt Preise der nächsten N Stunden zurück (für Dashboard-Chart)."""
        now = time.time()
        return [p for p in self._prices if p["start_ts"] >= now][:hours]

    def to_dict(self) -> dict:
        return {
            "provider": "awattar",
            "country": self.country,
            "current_price_ct": round(self._current_price_ct, 2),
            "markup_ct": self.markup_ct,
            "cost_limit_ct": self.cost_limit_ct,
            "is_cheap": self.is_cheap,
            "min_ct": round(self.min_price_ct, 2),
            "max_ct": round(self.max_price_ct, 2),
            "avg_ct": round(self.avg_price_ct, 2),
            "prices": self.get_prices(24),
            "error": self._error,
        }

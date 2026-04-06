"""Renault / Dacia — Fahrzeug-SoC via Gigya + Kamereon API.

Auth-Flow (wie evcc):
1. Gigya Login → Session Cookie
2. Gigya getJWT → JWT Token (15 Min)
3. Gigya getAccountInfo → Person ID
4. Kamereon /persons/{id} → Account ID
5. Kamereon /cars/{vin}/battery-status → SoC, Range, Ladestatus

Konfiguration:
{
    "manufacturer": "renault",
    "credentials": {"email": "...", "password": "..."},
    "vin": "VF1AG000...",
    "locale": "de_DE"
}
"""

import logging
import time
import requests as req

log = logging.getLogger("ems.vehicle.renault")

# API Keys (gleich wie evcc)
GIGYA_URL = "https://accounts.eu1.gigya.com"
GIGYA_API_KEY = "3_7PLksOyBRkHv126x5WhHb-5pqC1qFR8pQjxSeLB6nhAnPERTUlwnYoznHSxwX668"

KAMEREON_URL = "https://api-wired-prod-1-euw1.wrd-aws.com"
KAMEREON_API_KEY = "YjkKtHmGfaceeuExUDKGxrLZGGvtVS0J"

POLL_INTERVAL = 300  # 5 Minuten

# Locale → Country Mapping
LOCALE_COUNTRY = {
    "de_AT": "AT", "de_DE": "DE", "de_CH": "CH",
    "fr_FR": "FR", "en_GB": "GB", "it_IT": "IT",
    "es_ES": "ES", "nl_NL": "NL", "pt_PT": "PT",
}


class RenaultVehicle:
    """Holt SoC + Range von der Renault Kamereon API (synchron, wie evcc)."""

    def __init__(self, config: dict):
        self.vin = config.get("vin", "")
        self.name = config.get("name", "Renault")
        self.locale = config.get("locale", "de_DE")
        self.country = LOCALE_COUNTRY.get(self.locale, "DE")
        self._vehicle_config = config

        creds = config.get("credentials", {})
        self._email = creds.get("email", "")
        self._password = creds.get("password", "")

        # Auth state
        self._jwt_token: str = ""
        self._person_id: str = ""
        self._account_id: str = ""
        self._jwt_expires: float = 0

        # Vehicle data
        self._soc: float = 0
        self._range_km: float = 0
        self._charging: bool = False
        self._plugged_in: bool = False
        self._charge_power_w: float = 0
        self._remaining_min: int = 0
        self._last_poll: float = 0
        self._error: str | None = None

    def _gigya_login(self) -> str:
        """Gigya Login → Session Cookie."""
        resp = req.get(f"{GIGYA_URL}/accounts.login", params={
            "loginID": self._email,
            "password": self._password,
            "apiKey": GIGYA_API_KEY,
        }, timeout=15)
        data = resp.json()

        error_code = data.get("errorCode", 0)
        if error_code != 0:
            raise Exception(f"Gigya Login fehlgeschlagen: {error_code} — {data.get('errorMessage', '?')}")

        cookie = data.get("sessionInfo", {}).get("cookieValue", "")
        if not cookie:
            raise Exception("Gigya Login: kein Session Cookie erhalten")

        log.debug("Gigya Login OK")
        return cookie

    def _gigya_jwt(self, cookie: str) -> str:
        """Gigya Session Cookie → JWT Token."""
        resp = req.get(f"{GIGYA_URL}/accounts.getJWT", params={
            "apiKey": GIGYA_API_KEY,
            "login_token": cookie,
            "fields": "data.personId,data.gigyaDataCenter",
            "expiration": 900,
        }, timeout=15)
        data = resp.json()

        token = data.get("id_token", "")
        if not token:
            raise Exception(f"Gigya JWT fehlgeschlagen: {data.get('errorMessage', '?')}")

        self._jwt_expires = time.time() + 840  # 14 Min (Puffer)
        log.debug("Gigya JWT OK (gültig 15 Min)")
        return token

    def _gigya_person_id(self, cookie: str) -> str:
        """Gigya → Person ID."""
        resp = req.get(f"{GIGYA_URL}/accounts.getAccountInfo", params={
            "apiKey": GIGYA_API_KEY,
            "login_token": cookie,
        }, timeout=15)
        data = resp.json()

        person_id = data.get("data", {}).get("personId", "")
        if not person_id:
            raise Exception("Gigya: keine Person ID")

        log.debug("Person ID: %s", person_id)
        return person_id

    def _kamereon_headers(self) -> dict:
        """Headers für Kamereon API Calls."""
        return {
            "content-type": "application/vnd.api+json",
            "x-gigya-id_token": self._jwt_token,
            "apikey": KAMEREON_API_KEY,
        }

    def _kamereon_account_id(self) -> str:
        """Kamereon → Account ID (gefiltert nach Renault/Dacia)."""
        resp = req.get(
            f"{KAMEREON_URL}/commerce/v1/persons/{self._person_id}",
            headers=self._kamereon_headers(),
            params={"country": self.country},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for account in data.get("accounts", []):
            account_type = account.get("accountType", "")
            if account_type in ("MYRENAULT", "MYDACIA"):
                account_id = account.get("accountId", "")
                if account_id:
                    log.debug("Account ID: %s (%s)", account_id, account_type)
                    return account_id

        raise Exception("Kein Renault/Dacia Account gefunden")

    def _ensure_auth(self):
        """Stellt sicher dass JWT gültig ist, loggt sich bei Bedarf ein."""
        if self._jwt_token and time.time() < self._jwt_expires:
            return  # Token noch gültig

        log.info("Renault %s: Login mit %s...", self.name, self._email)
        cookie = self._gigya_login()
        self._jwt_token = self._gigya_jwt(cookie)
        self._person_id = self._gigya_person_id(cookie)
        self._account_id = self._kamereon_account_id()
        log.info("Renault API Login OK — Account: %s", self._account_id)

    def _fetch_battery_status(self):
        """Holt Battery-Status von der Kamereon API."""
        self._ensure_auth()

        url = (f"{KAMEREON_URL}/commerce/v1/accounts/{self._account_id}"
               f"/kamereon/kca/car-adapter/v2/cars/{self.vin}/battery-status")

        resp = req.get(
            url,
            headers=self._kamereon_headers(),
            params={"country": self.country},
            timeout=15,
        )

        if resp.status_code == 401:
            # Token abgelaufen → neu einloggen und retry
            log.info("Renault: Token abgelaufen — erneuter Login")
            self._jwt_token = ""
            self._ensure_auth()
            resp = req.get(url, headers=self._kamereon_headers(),
                          params={"country": self.country}, timeout=15)

        resp.raise_for_status()
        data = resp.json()

        attrs = data.get("data", {}).get("attributes", {})
        if "batteryLevel" in attrs and attrs["batteryLevel"] is not None:
            self._soc = float(attrs["batteryLevel"])
        if "batteryAutonomy" in attrs and attrs["batteryAutonomy"] is not None:
            self._range_km = float(attrs["batteryAutonomy"])
        if "chargingStatus" in attrs and attrs["chargingStatus"] is not None:
            self._charging = float(attrs["chargingStatus"]) >= 0.5
        if "plugStatus" in attrs and attrs["plugStatus"] is not None:
            self._plugged_in = int(attrs["plugStatus"]) >= 1
        if "instantaneousPower" in attrs and attrs["instantaneousPower"] is not None:
            self._charge_power_w = float(attrs["instantaneousPower"])
        if "chargingRemainingTime" in attrs and attrs["chargingRemainingTime"] is not None:
            self._remaining_min = int(attrs["chargingRemainingTime"])

    def poll(self):
        """Synchroner Poll — alle 5 Minuten SoC abfragen."""
        now = time.time()
        if now - self._last_poll < POLL_INTERVAL:
            return

        if not self._email or not self._password:
            if not self._error:
                self._error = "Keine Zugangsdaten"
                log.warning("Renault %s: Keine Zugangsdaten — überspringe", self.name)
            self._last_poll = now
            return

        try:
            self._fetch_battery_status()
            self._error = None
            self._last_poll = now
            log.info("Renault %s: SoC=%.0f%% Range=%.0fkm Charging=%s Plugged=%s Power=%.0fW Remaining=%dmin",
                     self.name, self._soc, self._range_km,
                     self._charging, self._plugged_in,
                     self._charge_power_w, self._remaining_min)

        except Exception as e:
            self._error = str(e)
            self._last_poll = now  # Nicht sofort erneut versuchen
            log.error("Renault %s Fehler: %s", self.name, e)

    @property
    def soc(self) -> float:
        return self._soc

    @property
    def range_km(self) -> float:
        return self._range_km

    @property
    def is_charging(self) -> bool:
        return self._charging

    @property
    def is_plugged_in(self) -> bool:
        return self._plugged_in

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "manufacturer": "renault",
            "vin": self.vin,
            "soc": self._soc,
            "range_km": self._range_km,
            "charging": self._charging,
            "plugged_in": self._plugged_in,
            "charge_power_w": self._charge_power_w,
            "remaining_min": self._remaining_min,
            "error": self._error,
            "last_poll": self._last_poll,
        }

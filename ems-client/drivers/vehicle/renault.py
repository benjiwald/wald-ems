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

# API Keys — Renault rotiert die Keys gelegentlich (Folge: 403042 INVALID_LOGINID
# beim Login mit ansonsten gueltigen Credentials). evcc hat ein KeyStore-System
# das die aktuellen Keys aus einem S3-Bucket holt — siehe _load_dynamic_keys().
# Die untenstehenden Werte sind nur Fallback wenn der S3-Bucket nicht erreichbar
# ist (entsprechen evcc-Fallback Stand 2026-05).
GIGYA_URL = "https://accounts.eu1.gigya.com"
GIGYA_API_KEY = "3_VgdkgtIRH3AdHvJm-cjV2ug2EFE0lxt0IJzMC4MFqZjFpn_GYFXVdNZ19L7wZX0N"

KAMEREON_URL = "https://api-wired-prod-1-euw1.wrd-aws.com"
KAMEREON_API_KEY = "YjkKtHmGfaceeuExUDKGxrLZGGvtVS0J"

# Dynamischer KeyStore (wie evcc)
KEYSTORE_URL = (
    "https://renault-wrd-prod-1-euw1-myrapp-one.s3-eu-west-1.amazonaws.com"
    "/configuration/android/config_{region}.json"
)
# Kamereon-Key wird im KeyStore manchmal mit dem falschen Wert ueberschrieben —
# evcc patcht ihn dann hart auf diesen bekannten guten Wert.
KAMEREON_KEY_OVERRIDE = "YjkKtHmGfaceeuExUDKGxrLZGGvtVS0J"

POLL_INTERVAL = 300         # 5 Minuten — idle / nicht aktiv ladend
POLL_INTERVAL_CHARGING = 120  # 2 Minuten — waehrend aktiver Ladung haeufiger,
                              # damit der Target-SoC-Stop schneller greift.
                              # Renault Rate-Limit ist eng — kleiner waere zu riskant.

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

        # Vehicle data — _soc=None solange noch nie ein valider Wert kam
        # (vorher initial 0 → loadpoint dachte "Auto ist leer", lud weiter).
        self._soc: float | None = None
        self._soc_last_valid_at: float = 0  # wann zuletzt ein plausibler Wert kam
        self._range_km: float = 0
        self._charging: bool = False
        self._plugged_in: bool = False
        self._charge_power_w: float = 0
        self._remaining_min: int = 0
        self._last_poll: float = 0
        self._last_poll_success: bool = False
        self._last_poll_at: float = 0   # erfolgreichster Poll
        self._last_error: str | None = None
        self._last_error_at: float = 0
        self._poll_count: int = 0
        self._poll_success_count: int = 0
        self._error: str | None = None
        # DB-Handle fuer Dashboard-Logs (vom main.py via attach_db gesetzt)
        self._db = None

        # Dynamische Keys (werden lazy beim ersten Login-Versuch geholt)
        self._gigya_url: str = GIGYA_URL
        self._gigya_key: str = GIGYA_API_KEY
        self._kamereon_url: str = KAMEREON_URL
        self._kamereon_key: str = KAMEREON_API_KEY
        self._keys_loaded: bool = False
        self._keys_loaded_at: float = 0

    def _load_dynamic_keys(self) -> None:
        """Holt aktuelle Renault-API-Keys aus dem S3-KeyStore (wie evcc).

        Renault rotiert die App-API-Keys gelegentlich. Statt sie hardcoded zu
        haben, holt evcc sie zur Laufzeit aus einem S3-Bucket. Wir machen
        das auch — fallback auf die hardcoded Werte wenn S3 nicht klappt.
        Cache fuer 24h damit wir nicht jeden Poll S3 hammern.
        """
        if self._keys_loaded and (time.time() - self._keys_loaded_at) < 86400:
            return
        # Region aus country ableiten (z.B. "DE" → "de_DE", aber S3 nutzt "de_DE"-Format)
        # Renault config_*.json existiert pro Region — wir nehmen einfach das Locale.
        region = self.locale  # z.B. "de_AT"
        try:
            url = KEYSTORE_URL.format(region=region)
            r = req.get(url, timeout=10)
            r.raise_for_status()
            cfg = r.json()
            servers = cfg.get("servers", {})
            gigya = servers.get("gigyaProd", {})
            kamereon = servers.get("wiredProd", {})
            new_gigya_url = gigya.get("target") or GIGYA_URL
            new_gigya_key = gigya.get("apikey") or GIGYA_API_KEY
            new_kam_url = kamereon.get("target") or KAMEREON_URL
            # Wie evcc: Kamereon-Key oft falsch im KeyStore → Override
            new_kam_key = KAMEREON_KEY_OVERRIDE
            if new_gigya_key != self._gigya_key:
                log.info("Renault %s: KeyStore-Update — neuer Gigya-Key (alter: ...%s, neuer: ...%s)",
                         self.name, self._gigya_key[-10:], new_gigya_key[-10:])
                if self._db is not None:
                    try:
                        self._db.publish_log("info",
                            f"Renault {self.name}: KeyStore aktualisiert ({region})")
                    except Exception:
                        pass
            self._gigya_url = new_gigya_url
            self._gigya_key = new_gigya_key
            self._kamereon_url = new_kam_url
            self._kamereon_key = new_kam_key
            self._keys_loaded = True
            self._keys_loaded_at = time.time()
        except Exception as e:
            log.warning("Renault %s: KeyStore-Fetch fehlgeschlagen (%s) — nutze Fallback-Keys",
                        self.name, e)
            # Trotzdem als "loaded" markieren damit wir nicht bei jedem Poll
            # neu versuchen — 1h spaeter retry
            self._keys_loaded = True
            self._keys_loaded_at = time.time() - 86400 + 3600

    def _gigya_login(self) -> str:
        """Gigya Login → Session Cookie.

        Bei Fehlern wird die KOMPLETTE Gigya-Response geloggt (ohne Passwort)
        damit man Auth-Probleme detailliert diagnostizieren kann. Plus
        Credentials-Diagnose (Laenge, Sonderzeichen) — fuer typische Probleme
        wie YAML-Escaping von '!', '#', '&', ':' im Passwort.
        """
        self._load_dynamic_keys()
        # evcc nutzt POST fuer accounts.login (vermeidet URL-Param-Logging in
        # Server-Logs + sicherer fuer Sonderzeichen). Versuche POST zuerst.
        try:
            resp = req.post(f"{self._gigya_url}/accounts.login", data={
                "loginID": self._email,
                "password": self._password,
                "apiKey": self._gigya_key,
            }, timeout=15)
            data = resp.json()
        except Exception as e:
            raise Exception(f"Gigya HTTP-Fehler: {e}")

        error_code = data.get("errorCode", 0)
        if error_code != 0:
            # Detail-Logging fuer Diagnose
            err_msg = data.get("errorMessage", "?")
            err_details = data.get("errorDetails", "")
            # Credentials-Form-Check
            email = self._email or ""
            pwd = self._password or ""
            cred_issues = []
            if not email:
                cred_issues.append("email-leer")
            elif "@" not in email:
                cred_issues.append("email-ohne-@")
            elif email != email.strip():
                cred_issues.append("email-mit-whitespace")
            if not pwd:
                cred_issues.append("passwort-leer")
            elif pwd != pwd.strip():
                cred_issues.append("passwort-mit-whitespace")
            elif any(ord(c) > 127 for c in pwd):
                cred_issues.append("passwort-hat-non-ascii")
            details = (f"errorCode={error_code} ({err_msg}) "
                       f"errorDetails={err_details!r} "
                       f"email_len={len(email)} pwd_len={len(pwd)} "
                       f"api_key=...{self._gigya_key[-12:]} "
                       f"cred_issues={cred_issues or 'keine'}")
            log.error("Gigya Login Detail: %s", details)
            if self._db is not None:
                try:
                    self._db.publish_log("error",
                        f"Renault {self.name}: Gigya {error_code} {err_msg} | "
                        f"email_len={len(email)} pwd_len={len(pwd)} "
                        f"key=...{self._gigya_key[-8:]} {cred_issues or 'keine'}")
                except Exception:
                    pass
            raise Exception(f"Gigya Login fehlgeschlagen: {error_code} — {err_msg}")

        cookie = data.get("sessionInfo", {}).get("cookieValue", "")
        if not cookie:
            raise Exception("Gigya Login: kein Session Cookie erhalten")

        log.debug("Gigya Login OK")
        return cookie

    def _gigya_jwt(self, cookie: str) -> str:
        """Gigya Session Cookie → JWT Token."""
        resp = req.get(f"{self._gigya_url}/accounts.getJWT", params={
            "apiKey": self._gigya_key,
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
        resp = req.get(f"{self._gigya_url}/accounts.getAccountInfo", params={
            "apiKey": self._gigya_key,
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
            "apikey": self._kamereon_key,
        }

    def _kamereon_account_id(self) -> str:
        """Kamereon → Account ID (gefiltert nach Renault/Dacia)."""
        resp = req.get(
            f"{self._kamereon_url}/commerce/v1/persons/{self._person_id}",
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

        url = (f"{self._kamereon_url}/commerce/v1/accounts/{self._account_id}"
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
            raw_soc = float(attrs["batteryLevel"])
            # Sanity-Filter: 0% liefert die Kamereon-API gelegentlich wenn das
            # Auto schlaeft / nicht antwortet — das ist KEIN gueltiger Wert.
            # 1..100% akzeptieren, alles andere als invalid behandeln.
            if 1.0 <= raw_soc <= 100.0:
                self._soc = raw_soc
                self._soc_last_valid_at = time.time()
            else:
                log.warning("Renault %s: API lieferte implausible SoC=%.1f%% — ignoriert",
                            self.name, raw_soc)
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

    def poll(self, force: bool = False):
        """Synchroner Poll — alle 5 Min idle, alle 2 Min waehrend aktiver Ladung.

        force=True ignoriert das Interval (fuer Manual-Trigger via Dashboard).
        """
        now = time.time()
        interval = POLL_INTERVAL_CHARGING if self._charging else POLL_INTERVAL
        if not force and (now - self._last_poll < interval):
            return

        if not self._email or not self._password:
            if not self._error:
                self._error = "Keine Zugangsdaten"
                log.warning("Renault %s: Keine Zugangsdaten — überspringe", self.name)
                if self._db is not None:
                    try:
                        self._db.publish_log("warning", f"Renault {self.name}: Keine Zugangsdaten in YAML")
                    except Exception:
                        pass
            self._last_poll = now
            return

        self._poll_count += 1
        try:
            self._fetch_battery_status()
            self._error = None
            self._last_error = None
            self._last_poll = now
            self._last_poll_success = True
            self._last_poll_at = now
            self._poll_success_count += 1
            soc_str = f"{self._soc:.0f}%" if self._soc is not None else "—"
            msg = (f"Renault {self.name}: SoC={soc_str} Range={self._range_km:.0f}km "
                   f"Charging={self._charging} Plugged={self._plugged_in} "
                   f"Power={self._charge_power_w:.0f}W Remaining={self._remaining_min}min")
            log.info(msg)
            # Erst-Poll + alle 30 Min in DB damit man im Dashboard sieht ob's lebt
            if self._db is not None and (self._poll_success_count == 1
                                          or self._poll_success_count % 15 == 0):
                try:
                    self._db.publish_log("info", msg)
                except Exception:
                    pass

        except Exception as e:
            err_str = str(e)
            self._error = err_str
            self._last_error = err_str
            self._last_error_at = now
            self._last_poll = now
            self._last_poll_success = False
            log.error("Renault %s Fehler: %s", self.name, e)
            # IMMER in DB schreiben damit man Auth-Probleme & Co. sieht
            if self._db is not None:
                try:
                    self._db.publish_log("error", f"Renault {self.name}: {err_str}")
                except Exception:
                    pass

    @property
    def soc(self) -> float | None:
        """Letzter gueltiger SoC-Wert aus der Renault Kamereon API.

        Returns None solange noch kein valider Wert empfangen wurde, oder
        wenn der letzte erfolgreiche Poll laenger als 1 h zurueckliegt (die
        Cloud-Daten gelten dann als veraltet und sollten nicht blindlings
        als 'aktueller Stand' verwendet werden).
        """
        if self._soc is None:
            return None
        # Stale-Check: > 60 min ohne Update → None
        if self._soc_last_valid_at and (time.time() - self._soc_last_valid_at) > 3600:
            return None
        return self._soc

    def diagnostic(self) -> dict:
        """State des Vehicle-Drivers fuer Dashboard-Diagnose."""
        now = time.time()
        return {
            "name": self.name,
            "vin": (self.vin[:5] + "..." + self.vin[-4:]) if len(self.vin) > 9 else "—",
            "has_credentials": bool(self._email and self._password),
            "auth_ok": bool(self._jwt_token and self._account_id),
            "poll_count": self._poll_count,
            "poll_success_count": self._poll_success_count,
            "last_poll_at": self._last_poll_at,
            "last_poll_age_s": round(now - self._last_poll_at, 1) if self._last_poll_at else None,
            "last_poll_attempt_age_s": round(now - self._last_poll, 1) if self._last_poll else None,
            "last_poll_success": self._last_poll_success,
            "last_error": self._last_error,
            "last_error_age_s": round(now - self._last_error_at, 1) if self._last_error_at else None,
            "soc": self._soc,
            "soc_age_s": round(now - self._soc_last_valid_at, 1) if self._soc_last_valid_at else None,
            "charging": self._charging,
            "plugged_in": self._plugged_in,
            "charge_power_w": self._charge_power_w,
        }

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

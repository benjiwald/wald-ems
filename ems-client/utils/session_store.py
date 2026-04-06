"""Session Store — Uploads completed charging sessions to Supabase."""

import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger("ems.sessions")


class SessionStore:
    """Sendet abgeschlossene Ladesessions an Supabase."""

    def __init__(self, hardware_id: str, supabase_url: str, supabase_anon: str):
        self.hardware_id = hardware_id
        self.supabase_url = supabase_url
        self.headers = {
            "apikey": supabase_anon,
            "Authorization": f"Bearer {supabase_anon}",
            "Content-Type": "application/json",
        }

    def upload_session(self, session: dict) -> bool:
        """Sendet eine abgeschlossene Session an Supabase."""
        try:
            resp = requests.post(
                f"{self.supabase_url}/rest/v1/rpc/insert_charging_session",
                headers=self.headers,
                json={
                    "p_hardware_id": self.hardware_id,
                    "p_loadpoint_id": session["loadpoint_id"],
                    "p_started_at": datetime.fromtimestamp(
                        session["started_at"], tz=timezone.utc
                    ).isoformat(),
                    "p_finished_at": datetime.fromtimestamp(
                        session.get("finished_at", session["started_at"]),
                        tz=timezone.utc,
                    ).isoformat() if session.get("finished_at") else None,
                    "p_energy_kwh": session.get("energy_kwh", 0),
                    "p_avg_power_w": session.get("avg_power_w", 0),
                    "p_max_power_w": session.get("max_power_w", 0),
                    "p_phases": session.get("phases", 3),
                    "p_mode": session.get("mode", "pv"),
                    "p_solar_pct": session.get("solar_pct", 0),
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Session hochgeladen: %.2f kWh", session.get("energy_kwh", 0))
            return True
        except Exception as e:
            log.warning("Session-Upload fehlgeschlagen: %s", e)
            return False

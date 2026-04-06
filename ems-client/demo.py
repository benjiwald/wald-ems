"""
Wald EMS — Demo-Modus
Generiert realistische Simulationsdaten wenn keine Hardware verfuegbar ist.
Aktivierung: demo: true in wald-ems.yaml oder WALD_EMS_DEMO=1
"""

import math
import random
import time
from datetime import datetime, timezone


class DemoSite:
    """Simulates a home energy system with PV, battery, grid, and EV charging."""

    def __init__(self, config=None):
        self.site_name = config.get("name", "Demo-Anlage") if config else "Demo-Anlage"
        self.grid_limit_kw = config.get("grid_limit_kw", 11) if config else 11
        self.battery_soc = 65.0  # Start at 65%
        self.battery_capacity_wh = 10000  # 10 kWh
        self.loadpoints = []
        self._last_update = time.time()

    def update(self):
        """Generate simulated site state based on time of day."""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        dt = time.time() - self._last_update
        self._last_update = time.time()

        # PV production: bell curve peaking at noon, 0 at night
        pv_peak = 8000  # 8kW peak
        if 6 < hour < 21:
            sun_factor = math.sin(math.pi * (hour - 6) / 15)  # 0 at 6h, peak at 13.5h, 0 at 21h
            cloud_factor = 0.7 + 0.3 * math.sin(time.time() / 300)  # slow cloud variation
            pv_w = max(0, pv_peak * sun_factor * cloud_factor + random.gauss(0, 100))
        else:
            pv_w = 0

        # Consumption: base load + peaks at morning/evening
        base_load = 400  # 400W base
        morning_peak = 1500 * max(0, math.exp(-((hour - 7.5) ** 2) / 2))
        evening_peak = 2500 * max(0, math.exp(-((hour - 19) ** 2) / 3))
        random_load = random.gauss(0, 150)
        consumption_w = max(200, base_load + morning_peak + evening_peak + random_load)

        # Battery: charges from PV excess, discharges when needed
        excess = pv_w - consumption_w
        battery_w = 0
        if excess > 500 and self.battery_soc < 98:
            # Charge battery (positive = charging)
            battery_w = min(excess * 0.7, 3000)  # Max 3kW charge
            charge_wh = battery_w * dt / 3600
            self.battery_soc = min(100, self.battery_soc + (charge_wh / self.battery_capacity_wh) * 100)
        elif excess < -200 and self.battery_soc > 10:
            # Discharge battery (negative = discharging)
            battery_w = max(excess * 0.6, -3000)  # Max 3kW discharge
            discharge_wh = abs(battery_w) * dt / 3600
            self.battery_soc = max(5, self.battery_soc - (discharge_wh / self.battery_capacity_wh) * 100)

        # Grid: whatever PV + battery can't cover
        grid_w = consumption_w - pv_w + battery_w  # positive = import, negative = export

        # Add small random noise
        pv_w = round(pv_w + random.gauss(0, 20), 1)
        consumption_w = round(consumption_w + random.gauss(0, 10), 1)
        grid_w = round(grid_w, 1)
        battery_w = round(battery_w, 1)

        return {
            "site_name": self.site_name + " (Demo)",
            "grid_w": grid_w,
            "pv_w": max(0, pv_w),
            "battery_w": battery_w,
            "battery_soc": round(self.battery_soc, 1),
            "consumption_w": max(0, consumption_w),
            "grid_limit_kw": self.grid_limit_kw,
            "loadpoints": [],
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def get_telemetry_metrics(self):
        """Return metrics for telemetry recording."""
        state = self.update()
        return [
            {"metric_type": "grid_w", "value": state["grid_w"], "unit": "W"},
            {"metric_type": "pv_w", "value": state["pv_w"], "unit": "W"},
            {"metric_type": "consumption_w", "value": state["consumption_w"], "unit": "W"},
            {"metric_type": "battery_w", "value": state["battery_w"], "unit": "W"},
            {"metric_type": "battery_soc", "value": state["battery_soc"], "unit": "%"},
        ]

"""EMS Local Dashboard — FastAPI Web-UI für den Endkunden.

Läuft auf dem Pi/Thin Client auf Port 8080.
Zeigt nur den eigenen Standort (evcc-ähnliches UI).

Starten: uvicorn ui.server:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mqtt_handler import VERSION

log = logging.getLogger("ems.ui")

UI_DIR = Path(__file__).parent
app = FastAPI(title="EMS Dashboard", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
templates = Jinja2Templates(directory=UI_DIR / "templates")

# Referenz zum ems-client Site-Objekt (wird von main.py gesetzt)
_site = None
_config = None  # ConfigManager Referenz für DB-Persist
_site_name = os.environ.get("EMS_SITE_NAME", "EMS Standort")
_ws_clients: list[WebSocket] = []


def set_site(site, site_name: str = "", config=None):
    """Wird von main.py aufgerufen um die Site-Referenz zu setzen."""
    global _site, _site_name, _config
    _site = site
    if config:
        _config = config
    if site_name:
        _site_name = site_name


def get_state() -> dict:
    """Baut den aktuellen State für UI und WebSocket."""
    if _site is None:
        return {"error": "Site nicht initialisiert"}

    state = _site._build_state()
    state["site_name"] = _site_name

    # Energiemix: 3 Quellen (PV, Batterie-Entladung, Netz-Import)
    pv_w = state.get("pv_w", 0)
    grid_w = state.get("grid_w", 0)
    battery_w = state.get("battery_w", 0)
    consumption_w = state.get("consumption_w", 0)

    pv_source = max(0, pv_w)
    bat_source = max(0, -battery_w)  # negativ = Entladung = Quelle
    grid_source = max(0, grid_w)     # positiv = Import
    total_source = pv_source + bat_source + grid_source

    if total_source > 10:
        state["pv_pct"] = round(pv_source / total_source * 100)
        state["bat_pct"] = round(bat_source / total_source * 100)
        state["grid_pct"] = 100 - state["pv_pct"] - state["bat_pct"]
    else:
        state["pv_pct"] = 0
        state["bat_pct"] = 0
        state["grid_pct"] = 0

    # Legacy (für Abwärtskompatibilität)
    state["self_consumption_pct"] = state["pv_pct"] + state["bat_pct"]
    state["feed_in_pct"] = 100 - state["self_consumption_pct"] if state["self_consumption_pct"] > 0 else 0

    # In/Out Bilanz
    battery_w = state.get("battery_w", 0)
    state["in_total_w"] = round(max(0, pv_w) + max(0, -battery_w) + max(0, grid_w))
    state["out_total_w"] = round(max(0, consumption_w) + max(0, battery_w) + max(0, -grid_w))

    # Kosten (aus site_config)
    grid_price = getattr(_site, "grid_price_eur_kwh", 0.27)
    feedin_price = getattr(_site, "feedin_price_eur_kwh", 0.065)
    state["grid_price_ct"] = round(grid_price * 100, 1)
    state["feedin_price_ct"] = round(feedin_price * 100, 1)

    # Restdauer + Fahrzeug-Infos pro Loadpoint anreichern
    for lp in state.get("loadpoints", []):
        soc = lp.get("vehicle_soc")
        target = lp.get("target_soc", 80)
        power_w = lp.get("charging_power_w", 0)
        status = lp.get("status", "A")  # A=kein Auto, B=verbunden, C=lädt

        # Fahrzeug-Infos + Wallbox-Session-Energie
        for site_lp in (_site.loadpoints if _site else []):
            if site_lp.id == lp.get("id"):
                vdrv = getattr(site_lp, '_vehicle_driver', None)
                if vdrv:
                    lp["vehicle_range_km"] = vdrv.range_km if status in ("B", "C") else 0

                # Wallbox-eigene Session-Energie direkt aus Charger-Cache lesen
                charger = site_lp.charger
                cache = getattr(charger, '_cache', {})
                # Probiere verschiedene Cache-Keys (je nach register_map)
                session_kwh = (
                    cache.get('energy_session', 0) or
                    cache.get('wallbox_energy_session', 0) or 0
                )
                if session_kwh and float(session_kwh) > 0.01:
                    lp["wallbox_session_kwh"] = round(float(session_kwh), 2)
                break

        # Restdauer NUR wenn tatsächlich geladen wird (Status C + Leistung > 100W)
        if status == "C" and power_w > 100 and soc and soc > 0 and target > soc:
            battery_kwh = 52  # Zoe ZE50, TODO: konfigurierbar machen
            remaining_kwh = (target - soc) / 100 * battery_kwh
            remaining_h = remaining_kwh / (power_w / 1000)
            lp["remaining_min"] = round(remaining_h * 60)

    return state


# ── HTML Routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    state = get_state()
    return templates.TemplateResponse(request, "index.html", {
        "state": state,
        "site_name": _site_name,
        "version": VERSION,
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    state = get_state()
    return templates.TemplateResponse(request, "settings.html", {
        "state": state,
        "site_name": _site_name,
        "version": VERSION,
    })


@app.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    state = get_state()
    return templates.TemplateResponse(request, "sessions.html", {
        "state": state,
        "site_name": _site_name,
        "version": VERSION,
    })


# ── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/state")
async def api_state():
    return JSONResponse(get_state())


@app.post("/api/loadpoint/{lp_id}/mode")
async def set_mode(lp_id: str, request: Request):
    body = await request.json()
    mode = body.get("mode", "")
    if _site:
        for lp in _site.loadpoints:
            if lp.id == lp_id:
                lp.set_mode(mode)
                # Mode in DB persistieren (sonst überschreibt Config-Refresh)
                if _config:
                    _config.update_loadpoint_mode(lp_id, mode)
                await broadcast_state()
                return {"ok": True, "mode": mode}
    return JSONResponse({"error": "Loadpoint nicht gefunden"}, 404)


@app.post("/api/loadpoint/{lp_id}/target-soc")
async def set_target_soc(lp_id: str, request: Request):
    body = await request.json()
    soc = float(body.get("soc", 80))
    if _site:
        for lp in _site.loadpoints:
            if lp.id == lp_id:
                lp.set_target_soc(soc)
                if _config:
                    _config.update_loadpoint_field(lp_id, "target_soc", soc)
                await broadcast_state()
                return {"ok": True, "target_soc": soc}
    return JSONResponse({"error": "Loadpoint nicht gefunden"}, 404)


@app.post("/api/loadpoint/{lp_id}/current")
async def set_current(lp_id: str, request: Request):
    body = await request.json()
    current = float(body.get("current", 16))
    if _site:
        for lp in _site.loadpoints:
            if lp.id == lp_id:
                lp.set_max_current(current)
                if _config:
                    _config.update_loadpoint_field(lp_id, "max_current", current)
                await broadcast_state()
                return {"ok": True, "max_current": current}
    return JSONResponse({"error": "Loadpoint nicht gefunden"}, 404)


@app.post("/api/loadpoint/{lp_id}/battery-boost")
async def set_battery_boost(lp_id: str, request: Request):
    body = await request.json()
    enable = bool(body.get("enable", False))
    if _site:
        for lp in _site.loadpoints:
            if lp.id == lp_id:
                lp.battery_boost = enable
                log.info("LP %s: Battery Boost → %s", lp.name, enable)
                await broadcast_state()
                return {"ok": True, "battery_boost": enable}
    return JSONResponse({"error": "Loadpoint nicht gefunden"}, 404)


@app.post("/api/loadpoint/{lp_id}/phases")
async def set_phases(lp_id: str, request: Request):
    body = await request.json()
    phases = int(body.get("phases", 3))
    if _site:
        for lp in _site.loadpoints:
            if lp.id == lp_id:
                lp.phases = max(1, min(3, phases))
                log.info("LP %s: Phasen → %d", lp.name, lp.phases)
                await broadcast_state()
                return {"ok": True, "phases": lp.phases}
    return JSONResponse({"error": "Loadpoint nicht gefunden"}, 404)


@app.post("/api/loadpoint/{lp_id}/min-soc")
async def set_min_soc(lp_id: str, request: Request):
    body = await request.json()
    soc = float(body.get("soc", 20))
    if _site:
        for lp in _site.loadpoints:
            if lp.id == lp_id:
                lp.set_min_soc(soc)
                if _config:
                    _config.update_loadpoint_field(lp_id, "min_soc", soc)
                await broadcast_state()
                return {"ok": True, "min_soc": soc}
    return JSONResponse({"error": "Loadpoint nicht gefunden"}, 404)


@app.get("/api/sessions")
async def api_sessions():
    """Gibt abgeschlossene Sessions zurück (lokal gespeicherte)."""
    if _site is None:
        return JSONResponse([])
    sessions = []
    for lp in _site.loadpoints:
        # Aktive Session
        if hasattr(lp, '_session') and lp._session:
            sessions.append(lp._session.to_dict())
        # Abgeschlossene (noch nicht hochgeladene)
        if hasattr(lp, '_completed_sessions'):
            sessions.extend(lp._completed_sessions)
    return JSONResponse(sessions)


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        # Initial state senden
        await ws.send_json(get_state())
        # Auf Client-Nachrichten warten (keepalive)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


async def broadcast_state():
    """Sendet aktuellen State an alle verbundenen WebSocket-Clients."""
    if not _ws_clients:
        return
    state = get_state()
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(state)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)

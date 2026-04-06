# Wald EMS — Architecture Notes

## Overview
Wald EMS is a self-hosted energy management system for Raspberry Pi, derived from the cloud-based Wania EMS. It combines the Next.js dashboard and Python Modbus client into a single local installation communicating via SQLite.

## Key Differences from Wania EMS
- **No Supabase** — SQLite replaces PostgreSQL
- **No MQTT** — Python writes directly to SQLite, Next.js reads from it
- **No multi-tenancy** — Single-site, no auth
- **YAML config** — `wald-ems.yaml` replaces DB-stored configuration
- **Two systemd services** — `wald-ems` (dashboard) + `wald-ems-client` (Modbus/control)

## Architecture

### Communication: SQLite (WAL mode)
- Python client writes `site_state` to `state` table every 10s
- Python client writes metrics to `telemetry` table every 30s
- Next.js reads via API routes, serves to browser
- Commands: Next.js inserts into `commands` table, Python polls every 1s
- SSE endpoint `/api/events` for real-time browser updates

### Dashboard (Next.js 15)
- `output: "standalone"` for Pi deployment
- `better-sqlite3` for database access (native module)
- Server-Sent Events for real-time updates
- No authentication (local network)
- Pages: `/` (dashboard), `/sessions`, `/settings`

### Python Client
- `db_handler.py` replaces `mqtt_handler.py`
- `config.py` reads YAML instead of Supabase RPC
- All drivers (Modbus, HTTP, vehicle APIs) unchanged from Wania
- 10s control loop, 30s telemetry interval

### Config
- Single YAML file: `wald-ems.yaml`
- Config path: `$WALD_EMS_CONFIG` → `./wald-ems.yaml` → `/opt/ems/wald-ems.yaml`
- Both Next.js and Python read the same file
- Python watches file mtime for hot-reload

## Build & Deploy
```bash
npm run build          # Next.js standalone build
npm run start          # Run standalone server
```

## Commands
```bash
npm run dev            # Development
npm run build          # Production build
npm run start          # Production server
```

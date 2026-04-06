-- Wald EMS SQLite Schema
-- Replaces Supabase PostgreSQL for fully local operation

PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

-- Current system state (written by Python every 10s, read by Next.js)
CREATE TABLE IF NOT EXISTS state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Telemetry time series
CREATE TABLE IF NOT EXISTS telemetry (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  metric      TEXT NOT NULL,
  value       REAL NOT NULL,
  unit        TEXT NOT NULL DEFAULT '',
  timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_telemetry_metric_ts ON telemetry(metric, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(timestamp DESC);

-- Charging sessions
CREATE TABLE IF NOT EXISTS charging_sessions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  loadpoint         TEXT NOT NULL,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  energy_kwh        REAL DEFAULT 0,
  solar_kwh         REAL DEFAULT 0,
  max_power_w       REAL DEFAULT 0,
  avg_power_w       REAL DEFAULT 0,
  mode              TEXT,
  phases            INTEGER DEFAULT 1,
  vehicle           TEXT,
  vehicle_soc_start REAL,
  vehicle_soc_end   REAL,
  cost_eur          REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON charging_sessions(started_at DESC);

-- Commands queue (written by Next.js, polled by Python)
CREATE TABLE IF NOT EXISTS commands (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  action       TEXT NOT NULL,
  payload      TEXT NOT NULL DEFAULT '{}',
  status       TEXT NOT NULL DEFAULT 'pending',
  result       TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_pending ON commands(status) WHERE status = 'pending';

-- Device logs
CREATE TABLE IF NOT EXISTS device_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  level      TEXT NOT NULL DEFAULT 'info',
  source     TEXT NOT NULL DEFAULT 'system',
  message    TEXT NOT NULL,
  metadata   TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON device_logs(created_at DESC);

-- Forecast data (solar + tariff)
CREATE TABLE IF NOT EXISTS forecasts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  type       TEXT NOT NULL,
  data       TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forecasts_type ON forecasts(type, fetched_at DESC);

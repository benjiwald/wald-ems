import Database from "better-sqlite3";
import fs from "fs";
import path from "path";
import { getConfig } from "./config";

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (_db) return _db;

  const config = getConfig();
  let dbPath = config.database?.path || "wald-ems.db";

  // Relative Pfade relativ zum Install-Verzeichnis aufloesen (nicht cwd)
  // Python-Client und Dashboard muessen dieselbe DB nutzen
  if (!path.isAbsolute(dbPath)) {
    const installDir = process.env.WALD_EMS_INSTALL_DIR || "/opt/ems";
    dbPath = path.join(installDir, dbPath);
  }

  // Ensure directory exists
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  _db = new Database(dbPath);
  _db.pragma("journal_mode = WAL");
  _db.pragma("busy_timeout = 5000");
  _db.pragma("foreign_keys = ON");

  // Run migrations
  const migrationsDir = path.join(process.cwd(), "migrations");
  if (fs.existsSync(migrationsDir)) {
    const files = fs.readdirSync(migrationsDir).filter(f => f.endsWith(".sql")).sort();
    for (const file of files) {
      const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");
      _db.exec(sql);
    }
  }

  return _db;
}

// ── State helpers ──

export function getState(key: string): Record<string, unknown> | null {
  const db = getDb();
  const row = db.prepare("SELECT value FROM state WHERE key = ?").get(key) as { value: string } | undefined;
  return row ? JSON.parse(row.value) : null;
}

export function setState(key: string, value: Record<string, unknown>): void {
  const db = getDb();
  db.prepare(
    "INSERT INTO state (key, value, updated_at) VALUES (?, ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
  ).run(key, JSON.stringify(value));
}

// ── Telemetry helpers ──

export interface TelemetryRow {
  id: number;
  metric: string;
  value: number;
  unit: string;
  timestamp: string;
}

export function getTelemetry(range: string, metric?: string): TelemetryRow[] {
  const db = getDb();
  const cutoffs: Record<string, string> = {
    "1h": "-1 hour",
    "6h": "-6 hours",
    "24h": "-24 hours",
    "7d": "-7 days",
    "30d": "-30 days",
  };
  const cutoff = cutoffs[range] || "-24 hours";

  if (metric) {
    return db.prepare(
      "SELECT id, metric, value, unit, timestamp FROM telemetry WHERE metric = ? AND timestamp >= datetime('now', ?) ORDER BY timestamp ASC"
    ).all(metric, cutoff) as TelemetryRow[];
  }

  return db.prepare(
    "SELECT id, metric, value, unit, timestamp FROM telemetry WHERE timestamp >= datetime('now', ?) ORDER BY timestamp ASC"
  ).all(cutoff) as TelemetryRow[];
}

// ── Commands helpers ──

export interface CommandRow {
  id: number;
  action: string;
  payload: string;
  status: string;
  result: string | null;
  created_at: string;
  processed_at: string | null;
}

export function createCommand(action: string, payload: Record<string, unknown> = {}): CommandRow {
  const db = getDb();
  const info = db.prepare(
    "INSERT INTO commands (action, payload) VALUES (?, ?)"
  ).run(action, JSON.stringify(payload));
  return db.prepare("SELECT * FROM commands WHERE id = ?").get(info.lastInsertRowid) as CommandRow;
}

// ── Logs helpers ──

export interface LogRow {
  id: number;
  level: string;
  source: string;
  message: string;
  metadata: string | null;
  created_at: string;
}

export function getLogs(limit = 100, level?: string): LogRow[] {
  const db = getDb();
  if (level) {
    return db.prepare(
      "SELECT * FROM device_logs WHERE level = ? ORDER BY created_at DESC LIMIT ?"
    ).all(level, limit) as LogRow[];
  }
  return db.prepare(
    "SELECT * FROM device_logs ORDER BY created_at DESC LIMIT ?"
  ).all(limit) as LogRow[];
}

// ── Sessions helpers ──

export interface SessionRow {
  id: number;
  loadpoint: string;
  started_at: string;
  finished_at: string | null;
  energy_kwh: number;
  solar_kwh: number;
  max_power_w: number;
  avg_power_w: number;
  mode: string | null;
  phases: number;
  vehicle: string | null;
  vehicle_soc_start: number | null;
  vehicle_soc_end: number | null;
  cost_eur: number;
}

export function getSessions(limit = 50): SessionRow[] {
  const db = getDb();
  // Ghost-Sessions mit 0 kWh entfernen
  db.prepare("DELETE FROM charging_sessions WHERE energy_kwh < 0.01").run();
  return db.prepare(
    "SELECT * FROM charging_sessions ORDER BY started_at DESC LIMIT ?"
  ).all(limit) as SessionRow[];
}

// ── Cleanup ──

export function cleanupOldData(retentionDays = 30): void {
  const db = getDb();
  db.prepare("DELETE FROM telemetry WHERE timestamp < datetime('now', ?)").run(`-${retentionDays} days`);
  db.prepare("DELETE FROM device_logs WHERE created_at < datetime('now', ?)").run(`-${retentionDays} days`);
  db.prepare("DELETE FROM commands WHERE status = 'done' AND created_at < datetime('now', '-7 days')").run();
}

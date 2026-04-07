import fs from "fs";
import path from "path";
import yaml from "js-yaml";

export interface WaldConfig {
  site: {
    name: string;
    grid_limit_kw: number;
    buffer_w: number;
    priority_soc: number;
    grid_price_eur_kwh: number;
    feedin_price_eur_kwh: number;
  };
  meters: Array<{
    name: string;
    type: string;
    host: string;
    port: number;
    unit_id: number;
    register_map?: Record<string, { register: number; type: string; scale: number }>;
  }>;
  chargers: Array<{
    name: string;
    type: string;
    host: string;
    port: number;
    unit_id: number;
  }>;
  loadpoints: Array<{
    name: string;
    charger: string;
    meter?: string;
    mode: string;
    min_current: number;
    max_current: number;
    phases: number;
    target_soc?: number;
    min_soc?: number;
    priority?: number;
  }>;
  vehicles: Array<{
    name: string;
    manufacturer: string;
    vin?: string;
    locale?: string;
    credentials?: Record<string, string>;
    loadpoint?: string;
    battery_kwh: number;
  }>;
  forecast?: {
    lat: number;
    lon: number;
    planes: Array<{
      name: string;
      kwp: number;
      declination: number;
      azimuth: number;
    }>;
  };
  tariff?: {
    provider: string;
    country: string;
    markup_ct?: number;
  };
  database: {
    path: string;
    retention_days: number;
  };
}

let _config: WaldConfig | null = null;
let _configMtime: number = 0;

function findConfigPath(): string {
  // 1. Environment variable
  if (process.env.WALD_EMS_CONFIG) return process.env.WALD_EMS_CONFIG;

  // 2. Current directory
  const local = path.join(process.cwd(), "wald-ems.yaml");
  if (fs.existsSync(local)) return local;

  // 3. Production default
  const prod = "/opt/ems/wald-ems.yaml";
  if (fs.existsSync(prod)) return prod;

  return local; // fallback
}

export function getConfig(): WaldConfig {
  const configPath = findConfigPath();

  // Re-read if file changed
  try {
    const stat = fs.statSync(configPath);
    if (_config && stat.mtimeMs === _configMtime) return _config;
    _configMtime = stat.mtimeMs;
  } catch {
    // File doesn't exist yet - return defaults
    if (_config) return _config;
    return getDefaults();
  }

  const raw = fs.readFileSync(configPath, "utf-8");
  _config = yaml.load(raw) as WaldConfig;

  // Apply defaults
  _config.database = _config.database || { path: path.join(process.cwd(), "wald-ems.db"), retention_days: 30 };
  _config.site = _config.site || getDefaults().site;
  _config.meters = _config.meters || [];
  _config.chargers = _config.chargers || [];
  _config.loadpoints = _config.loadpoints || [];
  _config.vehicles = _config.vehicles || [];

  return _config;
}

export function getConfigPath(): string {
  return findConfigPath();
}

function getDefaults(): WaldConfig {
  return {
    site: {
      name: "Mein Zuhause",
      grid_limit_kw: 11,
      buffer_w: 100,
      priority_soc: 0,
      grid_price_eur_kwh: 0.27,
      feedin_price_eur_kwh: 0.065,
    },
    meters: [],
    chargers: [],
    loadpoints: [],
    vehicles: [],
    database: {
      path: path.join(process.cwd(), "wald-ems.db"),
      retention_days: 30,
    },
  };
}

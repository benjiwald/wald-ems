import type { WaldConfig } from "./config";

// ---------------------------------------------------------------------------
// Field definition — describes a single configuration input
// ---------------------------------------------------------------------------

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  placeholder?: string;
  required?: boolean;
  options?: { value: string; label: string }[];
  min?: number;
  max?: number;
  step?: number;
  help?: string;
}

// ---------------------------------------------------------------------------
// Template interfaces
// ---------------------------------------------------------------------------

export interface MeterTemplate {
  type: string;
  label: string;
  description: string;
  icon: string;
  fields: FieldDef[];
  defaults: Record<string, any>;
}

export interface ChargerTemplate {
  type: string;
  label: string;
  description: string;
  icon: string;
  fields: FieldDef[];
  defaults: Record<string, any>;
}

export interface ChargingMode {
  mode: string;
  label: string;
  description: string;
  icon: string;
}

// ---------------------------------------------------------------------------
// Shared field builders
// ---------------------------------------------------------------------------

const hostField = (placeholder = "192.168.1.100"): FieldDef => ({
  key: "host",
  label: "IP-Adresse / Hostname",
  type: "text",
  placeholder,
  required: true,
  help: "IP-Adresse oder Hostname des Geräts im lokalen Netzwerk",
});

const portField = (defaultPort: number): FieldDef => ({
  key: "port",
  label: "Port",
  type: "number",
  placeholder: String(defaultPort),
  required: true,
  min: 1,
  max: 65535,
  step: 1,
  help: "TCP-Port für die Kommunikation",
});

const unitIdField = (defaultId: number): FieldDef => ({
  key: "unit_id",
  label: "Modbus Unit-ID",
  type: "number",
  placeholder: String(defaultId),
  required: true,
  min: 0,
  max: 247,
  step: 1,
  help: "Modbus Slave-Adresse des Geräts",
});

// ---------------------------------------------------------------------------
// METER TEMPLATES
// ---------------------------------------------------------------------------

export const METER_TEMPLATES: MeterTemplate[] = [
  {
    type: "victron_venus_system",
    label: "Victron Venus OS (System)",
    description:
      "Victron Cerbo GX / Venus OS — liest Grid, PV, Batterie und Verbrauch",
    icon: "⚡",
    fields: [hostField(), portField(502), unitIdField(100)],
    defaults: { host: "", port: 502, unit_id: 100 },
  },
  {
    type: "sma_sunnyboy",
    label: "SMA Sunny Boy / Tripower",
    description: "SMA Wechselrichter via Modbus TCP",
    icon: "☀️",
    fields: [hostField(), portField(502), unitIdField(3)],
    defaults: { host: "", port: 502, unit_id: 3 },
  },
  {
    type: "fronius_symo",
    label: "Fronius Symo / Gen24",
    description: "Fronius Wechselrichter via Modbus TCP",
    icon: "🔆",
    fields: [hostField(), portField(502), unitIdField(1)],
    defaults: { host: "", port: 502, unit_id: 1 },
  },
  {
    type: "shelly_em",
    label: "Shelly EM / 3EM / Pro",
    description: "Shelly Energy Meter via HTTP API",
    icon: "📊",
    fields: [
      hostField("192.168.1.100"),
      {
        key: "port",
        label: "Port",
        type: "number",
        placeholder: "80",
        required: true,
        min: 1,
        max: 65535,
        step: 1,
        help: "HTTP-Port (Standard: 80)",
      },
    ],
    defaults: { host: "", port: 80 },
  },
  {
    type: "custom_modbus",
    label: "Benutzerdefiniert (Modbus TCP)",
    description: "Eigene Modbus-Konfiguration",
    icon: "🔧",
    fields: [
      hostField(),
      portField(502),
      unitIdField(1),
      {
        key: "register_map",
        label: "Register-Map (JSON)",
        type: "text",
        placeholder: '{"grid_w": {"register": 0, "type": "int16", "scale": 1}}',
        required: false,
        help: 'JSON-Objekt mit Registeradressen, z.B. {"grid_w": {"register": 0, "type": "int16", "scale": 1}}',
      },
    ],
    defaults: { host: "", port: 502, unit_id: 1, register_map: "" },
  },
];

// ---------------------------------------------------------------------------
// CHARGER TEMPLATES
// ---------------------------------------------------------------------------

export const CHARGER_TEMPLATES: ChargerTemplate[] = [
  {
    type: "nrgkick_modbus",
    label: "NRG Kick (Modbus)",
    description: "NRG Kick mobile Wallbox via Modbus TCP",
    icon: "🔌",
    fields: [hostField(), portField(502), unitIdField(1)],
    defaults: { host: "", port: 502, unit_id: 1 },
  },
  {
    type: "go_e",
    label: "go-eCharger",
    description: "go-eCharger via HTTP API",
    icon: "🟢",
    fields: [hostField("192.168.1.100")],
    defaults: { host: "" },
  },
  {
    type: "easee",
    label: "Easee Home",
    description: "Easee Home Wallbox",
    icon: "🏠",
    fields: [hostField("192.168.1.100")],
    defaults: { host: "" },
  },
  {
    type: "custom_modbus",
    label: "Benutzerdefiniert (Modbus TCP)",
    description: "Eigene Modbus-Wallbox",
    icon: "🔧",
    fields: [hostField(), portField(502), unitIdField(1)],
    defaults: { host: "", port: 502, unit_id: 1 },
  },
];

// ---------------------------------------------------------------------------
// CHARGING MODES
// ---------------------------------------------------------------------------

export const CHARGING_MODES: ChargingMode[] = [
  {
    mode: "off",
    label: "Aus",
    description: "Laden deaktiviert",
    icon: "⏹️",
  },
  {
    mode: "now",
    label: "Sofort",
    description: "Laden mit maximaler Leistung",
    icon: "⚡",
  },
  {
    mode: "minpv",
    label: "Min+PV",
    description: "Mindestladung + PV-Überschuss",
    icon: "🌤️",
  },
  {
    mode: "pv",
    label: "Nur PV",
    description: "Nur bei PV-Überschuss laden",
    icon: "☀️",
  },
];

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

export function getMeterTemplate(type: string): MeterTemplate | undefined {
  return METER_TEMPLATES.find((t) => t.type === type);
}

export function getChargerTemplate(type: string): ChargerTemplate | undefined {
  return CHARGER_TEMPLATES.find((t) => t.type === type);
}

/**
 * Returns a minimal valid WaldConfig with empty device arrays.
 * Useful as a starting point for new installations.
 */
export function getDefaultConfig(): WaldConfig {
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
      path: "wald-ems.db",
      retention_days: 30,
    },
  };
}

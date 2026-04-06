"use client";

import { Plug, Car, Zap, ZapOff } from "lucide-react";

interface LoadpointProps {
  name: string;
  mode: string;
  status: string;
  power_w: number;
  current_a: number;
  phases: number;
  energy_kwh: number;
  vehicle?: string;
  vehicle_soc?: number;
  onModeChange: (mode: string) => void;
}

const MODES = [
  { value: "off", label: "Aus", icon: ZapOff },
  { value: "now", label: "Sofort", icon: Zap },
  { value: "min_pv", label: "Min+PV", icon: Plug },
  { value: "pv", label: "PV", icon: Plug },
];

function formatPower(watts: number): string {
  if (watts >= 1000) return `${(watts / 1000).toFixed(1)} kW`;
  return `${Math.round(watts)} W`;
}

export default function LoadpointCard({
  name, mode, status, power_w, current_a, phases, energy_kwh,
  vehicle, vehicle_soc, onModeChange,
}: LoadpointProps) {
  const isCharging = status === "charging";

  return (
    <div className="glass-panel rounded-2xl p-5 card-hover">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="font-semibold text-base">{name}</h3>
          <p className={`text-xs mt-1 ${isCharging ? "text-primary" : "text-muted-foreground"}`}>
            {isCharging ? "Laedt..." : status === "connected" ? "Verbunden" : "Getrennt"}
          </p>
        </div>
        {isCharging && (
          <div className="w-3 h-3 rounded-full bg-primary animate-pulse-glow" />
        )}
      </div>

      {/* Power display */}
      <div className="mb-4">
        <p className="mono text-2xl font-bold">{formatPower(power_w)}</p>
        <p className="text-xs text-muted-foreground">
          {current_a || 0}A / {phases}P &middot; {(energy_kwh || 0).toFixed(1)} kWh
        </p>
      </div>

      {/* Vehicle info */}
      {vehicle && (
        <div className="flex items-center gap-2 mb-4 p-2 rounded-lg bg-muted/50">
          <Car className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm">{vehicle}</span>
          {vehicle_soc != null && (
            <span className="ml-auto mono text-sm font-medium">{vehicle_soc}%</span>
          )}
        </div>
      )}

      {/* Mode selector */}
      <div className="grid grid-cols-4 gap-1 bg-muted/50 rounded-xl p-1">
        {MODES.map(m => (
          <button
            key={m.value}
            onClick={() => onModeChange(m.value)}
            className={`flex flex-col items-center gap-1 py-2 px-1 rounded-lg text-xs transition-all ${
              mode === m.value
                ? "bg-primary text-primary-foreground shadow-sm"
                : "hover:bg-muted text-muted-foreground"
            }`}
          >
            <m.icon className="w-3.5 h-3.5" />
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}

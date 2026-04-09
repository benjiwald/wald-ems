"use client";

import { Plug, Car, Zap, ZapOff, Clock } from "lucide-react";

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
  target_soc?: number;
  min_soc?: number;
  battery_kwh?: number;
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

function formatDuration(minutes: number): string {
  if (minutes < 1) return "< 1 Min";
  if (minutes < 60) return `${Math.round(minutes)} Min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function calcTimeToTarget(
  currentSoc: number, targetSoc: number, batteryKwh: number, powerW: number
): number | null {
  if (powerW < 100 || currentSoc >= targetSoc || batteryKwh <= 0) return null;
  const remainingKwh = ((targetSoc - currentSoc) / 100) * batteryKwh;
  const hours = remainingKwh / (powerW / 1000);
  return hours * 60; // minutes
}

export default function LoadpointCard({
  name, mode, status, power_w, current_a, phases, energy_kwh,
  vehicle, vehicle_soc, target_soc, min_soc, battery_kwh, onModeChange,
}: LoadpointProps) {
  // IEC 61851 Status: A=getrennt, B=verbunden, C=laden, F=fehler
  const isCharging = status === "C" || status === "charging";
  const isConnected = status === "B" || status === "connected";
  const effectiveTargetSoc = target_soc || 100;
  const timeToTarget = (vehicle_soc != null && battery_kwh)
    ? calcTimeToTarget(vehicle_soc, effectiveTargetSoc, battery_kwh, power_w)
    : null;

  return (
    <div className="glass-panel rounded-2xl p-5 card-hover">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="font-semibold text-base">{name}</h3>
          <p className={`text-xs mt-1 ${isCharging ? "text-primary" : "text-muted-foreground"}`}>
            {isCharging ? "Laedt..." : isConnected ? "Verbunden" : status === "F" ? "Fehler" : "Getrennt"}
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

      {/* Vehicle info with SoC bar */}
      {(vehicle || vehicle_soc != null) && (
        <div className="mb-4 p-3 rounded-xl bg-muted/50 space-y-2">
          <div className="flex items-center gap-2">
            <Car className="w-4 h-4 text-muted-foreground" />
            <span className="text-sm">{vehicle || "Fahrzeug"}</span>
            {vehicle_soc != null && (
              <span className="ml-auto mono text-sm font-medium">{vehicle_soc}%</span>
            )}
          </div>
          {/* SoC Progress Bar */}
          {vehicle_soc != null && (
            <div className="relative h-2.5 bg-muted rounded-full overflow-hidden">
              <div
                className={`absolute inset-y-0 left-0 rounded-full transition-all duration-500 ${
                  vehicle_soc < (min_soc || 20) ? "bg-destructive" :
                  vehicle_soc >= effectiveTargetSoc ? "bg-primary" : "bg-primary/70"
                }`}
                style={{ width: `${Math.min(100, vehicle_soc)}%` }}
              />
              {/* Target SoC marker */}
              {effectiveTargetSoc < 100 && (
                <div
                  className="absolute inset-y-0 w-0.5 bg-foreground/40"
                  style={{ left: `${effectiveTargetSoc}%` }}
                  title={`Ziel: ${effectiveTargetSoc}%`}
                />
              )}
            </div>
          )}
          {/* Time to target */}
          {isCharging && timeToTarget != null && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock className="w-3 h-3" />
              <span>~{formatDuration(timeToTarget)} bis {effectiveTargetSoc}%</span>
            </div>
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

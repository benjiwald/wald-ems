"use client";

import { Plug, Car, Zap, ZapOff, Clock, Battery } from "lucide-react";

interface LoadpointProps {
  name: string;
  mode: string;
  status: string;
  power_w: number;
  current_a: number;
  phases: number;
  active_phases?: number;
  currents?: number[] | null;
  voltages?: number[] | null;
  apparent_va?: number | null;
  power_factor?: number | null;
  energy_kwh: number;
  vehicle?: string;
  vehicle_soc?: number;
  target_soc?: number;
  min_soc?: number;
  battery_kwh?: number;
  battery_boost?: boolean;
  onModeChange: (mode: string) => void;
  onBatteryBoostChange?: (enable: boolean) => void;
  onTargetSocChange?: (value: number) => void;
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
  name, mode, status, power_w, current_a, phases, active_phases, currents, voltages,
  apparent_va, power_factor, energy_kwh,
  vehicle, vehicle_soc, target_soc, min_soc, battery_kwh, battery_boost,
  onModeChange, onBatteryBoostChange, onTargetSocChange,
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
          {current_a || 0}A / {active_phases ?? phases}P &middot; {(energy_kwh || 0).toFixed(1)} kWh
          {active_phases != null && active_phases !== phases && isCharging && (
            <span className="ml-1 text-amber-500">({phases}P konfiguriert)</span>
          )}
        </p>
        {/* Live-Phasenströme L1/L2/L3 */}
        {currents && isCharging && (
          <p className="text-xs mono text-muted-foreground mt-0.5">
            L1:{currents[0]?.toFixed(1)}A &middot; L2:{currents[1]?.toFixed(1)}A &middot; L3:{currents[2]?.toFixed(1)}A
          </p>
        )}
        {/* Spannungen + Scheinleistung + PF */}
        {voltages && isCharging && (
          <p className="text-xs mono text-muted-foreground mt-0.5">
            U: {voltages[0]?.toFixed(0)}/{voltages[1]?.toFixed(0)}/{voltages[2]?.toFixed(0)}V
            {apparent_va != null && ` · S=${(apparent_va/1000).toFixed(1)}kVA`}
            {power_factor != null && ` · PF=${power_factor.toFixed(2)}`}
          </p>
        )}
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
          {/* Target SoC Slider */}
          {onTargetSocChange && (
            <div className="pt-1">
              <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                <span>Ziel-Ladestand</span>
                <span className="mono font-medium text-foreground">{effectiveTargetSoc}%</span>
              </div>
              <input
                type="range"
                min={50}
                max={100}
                step={5}
                value={effectiveTargetSoc}
                onChange={(e) => onTargetSocChange(Number(e.target.value))}
                className="w-full accent-primary cursor-pointer"
              />
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

      {/* Battery Boost Toggle */}
      {onBatteryBoostChange && (
        <button
          onClick={() => onBatteryBoostChange(!battery_boost)}
          title="Hausbatterie fuer schnelleres Laden entladen"
          className={`mt-2 w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl text-xs font-medium transition-all border ${
            battery_boost
              ? "bg-amber-500/20 text-amber-400 border-amber-500/40"
              : "bg-muted/30 text-muted-foreground border-transparent hover:bg-muted/50"
          }`}
        >
          <Battery className="w-3.5 h-3.5" />
          Battery Boost {battery_boost ? "AN" : "AUS"}
        </button>
      )}
    </div>
  );
}

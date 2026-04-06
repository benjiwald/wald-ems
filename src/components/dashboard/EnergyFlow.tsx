"use client";

import { Sun, Plug, Battery, Home, ArrowRight } from "lucide-react";

interface EnergyFlowProps {
  grid_w: number;
  pv_w: number;
  battery_w: number;
  battery_soc: number;
  consumption_w: number;
}

function formatPower(watts: number): string {
  if (Math.abs(watts) >= 1000) return `${(watts / 1000).toFixed(1)} kW`;
  return `${Math.round(watts)} W`;
}

function PowerNode({ icon: Icon, label, value, color, subtitle }: {
  icon: React.ElementType;
  label: string;
  value: number;
  color: string;
  subtitle?: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div className={`w-16 h-16 rounded-2xl flex items-center justify-center ${color}`}>
        <Icon className="w-7 h-7" />
      </div>
      <div className="text-center">
        <p className="mono text-lg font-semibold">{formatPower(Math.abs(value))}</p>
        <p className="text-xs text-muted-foreground">{label}</p>
        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
      </div>
    </div>
  );
}

export default function EnergyFlow({ grid_w, pv_w, battery_w, battery_soc, consumption_w }: EnergyFlowProps) {
  return (
    <div className="glass-panel rounded-2xl p-6">
      <h2 className="text-sm font-medium text-muted-foreground mb-6">Energiefluss</h2>
      <div className="flex items-center justify-around gap-2 flex-wrap">
        <PowerNode
          icon={Sun}
          label="PV"
          value={pv_w}
          color="bg-amber-500/20 text-amber-400"
        />
        {pv_w > 0 && (
          <ArrowRight className="w-5 h-5 text-muted-foreground shrink-0" />
        )}
        <PowerNode
          icon={Home}
          label="Verbrauch"
          value={consumption_w}
          color="bg-blue-500/20 text-blue-400"
        />
        <div className="w-px h-12 bg-border mx-2 hidden sm:block" />
        <PowerNode
          icon={Plug}
          label={grid_w > 0 ? "Netzbezug" : "Einspeisung"}
          value={grid_w}
          color={grid_w > 0 ? "bg-red-500/20 text-red-400" : "bg-green-500/20 text-green-400"}
        />
        <PowerNode
          icon={Battery}
          label={battery_w > 0 ? "Laden" : battery_w < 0 ? "Entladen" : "Batterie"}
          value={battery_w}
          color="bg-purple-500/20 text-purple-400"
          subtitle={`${battery_soc}%`}
        />
      </div>
    </div>
  );
}

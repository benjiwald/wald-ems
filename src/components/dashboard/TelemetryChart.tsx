"use client";

import { useState, useEffect, useCallback } from "react";
import { XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart, Line, ComposedChart } from "recharts";

interface ChartData {
  timestamp: string;
  grid_w: number;
  pv_w: number;
  consumption_w: number;
  battery_w: number;
  battery_soc: number;
}

const RANGES = ["1h", "6h", "24h", "7d"] as const;

const SERIES = [
  { key: "pv_w", name: "PV", color: "#f59e0b", axis: "power" },
  { key: "consumption_w", name: "Verbrauch", color: "#3b82f6", axis: "power" },
  { key: "grid_w", name: "Netz", color: "#ef4444", axis: "power" },
  { key: "battery_w", name: "Batterie", color: "#a855f7", axis: "power" },
  { key: "battery_soc", name: "Speicher %", color: "#22c55e", axis: "soc" },
] as const;

export default function TelemetryChart() {
  const [range, setRange] = useState<string>("24h");
  const [data, setData] = useState<ChartData[]>([]);
  const [loading, setLoading] = useState(true);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const toggleSeries = useCallback((key: string) => {
    setHidden(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/telemetry?range=${range}`)
      .then(r => r.json())
      .then(json => {
        const metrics = json.metrics || {};
        const timeMap = new Map<string, ChartData>();

        for (const [metric, points] of Object.entries(metrics) as [string, Array<{ value: number; timestamp: string }>][]) {
          for (const p of points) {
            if (!timeMap.has(p.timestamp)) {
              timeMap.set(p.timestamp, { timestamp: p.timestamp, grid_w: 0, pv_w: 0, consumption_w: 0, battery_w: 0, battery_soc: 0 });
            }
            const entry = timeMap.get(p.timestamp)!;
            if (metric in entry) (entry as unknown as Record<string, number>)[metric] = p.value;
          }
        }

        setData(Array.from(timeMap.values()).sort((a, b) => a.timestamp.localeCompare(b.timestamp)));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [range]);

  const formatTime = (ts: string) => {
    const d = new Date(ts + "Z");
    if (range === "7d") return d.toLocaleDateString("de", { weekday: "short" });
    return d.toLocaleTimeString("de", { hour: "2-digit", minute: "2-digit" });
  };

  const hasSoc = !hidden.has("battery_soc");

  return (
    <div className="glass-panel rounded-2xl p-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-medium text-muted-foreground">Leistungsverlauf</h2>
        <div className="flex gap-1 bg-muted/50 rounded-lg p-1">
          {RANGES.map(r => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`px-3 py-1 rounded-md text-xs transition-colors ${
                range === r ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {/* Custom Legend — klickbar zum Ein/Ausblenden */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mb-3">
        {SERIES.map(s => {
          const isHidden = hidden.has(s.key);
          return (
            <button
              key={s.key}
              onClick={() => toggleSeries(s.key)}
              className={`flex items-center gap-1.5 text-xs transition-opacity ${isHidden ? "opacity-30" : "opacity-100"}`}
            >
              <span className="w-3 h-0.5 rounded-full" style={{ backgroundColor: s.color }} />
              <span className={isHidden ? "line-through" : ""}>{s.name}</span>
            </button>
          );
        })}
      </div>

      <div className="h-64">
        {loading ? (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">Laden...</div>
        ) : data.length === 0 ? (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
            Noch keine Daten vorhanden
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data}>
              <XAxis dataKey="timestamp" tickFormatter={formatTime} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
              {/* Linke Y-Achse: Leistung (kW) */}
              <YAxis
                yAxisId="power"
                tick={{ fontSize: 11 }}
                stroke="var(--muted-foreground)"
                tickFormatter={v => `${(v / 1000).toFixed(1)}kW`}
              />
              {/* Rechte Y-Achse: SoC (%) */}
              {hasSoc && (
                <YAxis
                  yAxisId="soc"
                  orientation="right"
                  domain={[0, 100]}
                  tick={{ fontSize: 11 }}
                  stroke="#22c55e"
                  tickFormatter={v => `${v}%`}
                />
              )}
              <Tooltip
                contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: "0.5rem", fontSize: 12 }}
                labelFormatter={(label) => formatTime(String(label))}
                formatter={(value: number, name: string) => {
                  if (name === "Speicher %") return [`${Math.round(value)}%`, name];
                  return [`${(value / 1000).toFixed(2)} kW`, name];
                }}
              />
              {/* Power series (Area) */}
              {!hidden.has("pv_w") && (
                <Area yAxisId="power" type="monotone" dataKey="pv_w" name="PV" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.1} strokeWidth={2} dot={false} />
              )}
              {!hidden.has("consumption_w") && (
                <Area yAxisId="power" type="monotone" dataKey="consumption_w" name="Verbrauch" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.1} strokeWidth={2} dot={false} />
              )}
              {!hidden.has("grid_w") && (
                <Area yAxisId="power" type="monotone" dataKey="grid_w" name="Netz" stroke="#ef4444" fill="#ef4444" fillOpacity={0.1} strokeWidth={1.5} dot={false} />
              )}
              {!hidden.has("battery_w") && (
                <Area yAxisId="power" type="monotone" dataKey="battery_w" name="Batterie" stroke="#a855f7" fill="#a855f7" fillOpacity={0.1} strokeWidth={1.5} dot={false} />
              )}
              {/* SoC series (Line, rechte Achse) */}
              {hasSoc && (
                <Line yAxisId="soc" type="monotone" dataKey="battery_soc" name="Speicher %" stroke="#22c55e" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

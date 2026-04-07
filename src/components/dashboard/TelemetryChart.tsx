"use client";

import { useState, useEffect } from "react";
import { XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart, Legend } from "recharts";

interface ChartData {
  timestamp: string;
  grid_w: number;
  pv_w: number;
  consumption_w: number;
  battery_w: number;
}

const RANGES = ["1h", "6h", "24h", "7d"] as const;

export default function TelemetryChart() {
  const [range, setRange] = useState<string>("24h");
  const [data, setData] = useState<ChartData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/telemetry?range=${range}`)
      .then(r => r.json())
      .then(json => {
        const metrics = json.metrics || {};
        // Merge all metrics into time-aligned data points
        const timeMap = new Map<string, ChartData>();

        for (const [metric, points] of Object.entries(metrics) as [string, Array<{ value: number; timestamp: string }>][]) {
          for (const p of points) {
            if (!timeMap.has(p.timestamp)) {
              timeMap.set(p.timestamp, { timestamp: p.timestamp, grid_w: 0, pv_w: 0, consumption_w: 0, battery_w: 0 });
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

  return (
    <div className="glass-panel rounded-2xl p-6">
      <div className="flex items-center justify-between mb-4">
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

      <div className="h-64">
        {loading ? (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">Laden...</div>
        ) : data.length === 0 ? (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
            Noch keine Daten vorhanden
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data}>
              <XAxis dataKey="timestamp" tickFormatter={formatTime} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
              <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={v => `${(v / 1000).toFixed(1)}kW`} />
              <Tooltip
                contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: "0.5rem", fontSize: 12 }}
                labelFormatter={(label) => formatTime(String(label))}
                formatter={(value) => [`${(Number(value) / 1000).toFixed(2)} kW`]}
              />
              <Legend
                verticalAlign="top"
                height={32}
                iconType="line"
                wrapperStyle={{ fontSize: 12, paddingBottom: 4 }}
              />
              <Area type="monotone" dataKey="pv_w" name="PV" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.1} strokeWidth={2} dot={false} />
              <Area type="monotone" dataKey="consumption_w" name="Verbrauch" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.1} strokeWidth={2} dot={false} />
              <Area type="monotone" dataKey="grid_w" name="Netz" stroke="#ef4444" fill="#ef4444" fillOpacity={0.1} strokeWidth={1.5} dot={false} />
              <Area type="monotone" dataKey="battery_w" name="Batterie" stroke="#a855f7" fill="#a855f7" fillOpacity={0.1} strokeWidth={1.5} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

"use client";

import { useState, useEffect, useMemo } from "react";
import Header from "@/components/dashboard/Header";
import { Sun, Plug2, Zap, Clock, Car, BarChart3 } from "lucide-react";

interface Session {
  id: number;
  loadpoint: string;
  started_at: string;
  finished_at: string | null;
  energy_kwh: number;
  solar_kwh: number;
  grid_kwh?: number;
  max_power_w: number;
  avg_power_w: number;
  mode: string | null;
  phases?: number;
  vehicle: string | null;
  vehicle_soc_start?: number | null;
  vehicle_soc_end?: number | null;
  cost_eur: number;
}

const MODE_LABEL: Record<string, string> = {
  now: "Sofort",
  pv: "PV",
  min_pv: "Min+PV",
  off: "Aus",
};

function formatDuration(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function durationFromStrings(start: string, end: string | null): number {
  const s = new Date(start + "Z").getTime();
  const e = end ? new Date(end + "Z").getTime() : Date.now();
  return Math.max(0, Math.floor((e - s) / 1000));
}

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);

  useEffect(() => {
    fetch("/api/sessions?limit=500")
      .then(r => r.json())
      .then(setSessions)
      .catch(() => {});
  }, []);

  const stats = useMemo(() => {
    if (sessions.length === 0) return null;
    const total_kwh = sessions.reduce((a, s) => a + (s.energy_kwh || 0), 0);
    const solar_kwh = sessions.reduce((a, s) => a + (s.solar_kwh || 0), 0);
    const grid_kwh = sessions.reduce(
      (a, s) => a + (s.grid_kwh ?? Math.max(0, (s.energy_kwh || 0) - (s.solar_kwh || 0))),
      0
    );
    const total_cost = sessions.reduce((a, s) => a + (s.cost_eur || 0), 0);
    const total_duration_s = sessions.reduce(
      (a, s) => a + durationFromStrings(s.started_at, s.finished_at),
      0
    );
    const count = sessions.length;
    const avg_session_kwh = count > 0 ? total_kwh / count : 0;
    const solar_share = total_kwh > 0 ? (solar_kwh / total_kwh) * 100 : 0;
    // Letzte 30 Tage für Zeitreihe
    const byDay = new Map<string, { solar: number; grid: number }>();
    for (const s of sessions) {
      const day = s.started_at.slice(0, 10); // YYYY-MM-DD
      const g = s.grid_kwh ?? Math.max(0, (s.energy_kwh || 0) - (s.solar_kwh || 0));
      const cur = byDay.get(day) || { solar: 0, grid: 0 };
      cur.solar += s.solar_kwh || 0;
      cur.grid += g;
      byDay.set(day, cur);
    }
    const days = Array.from(byDay.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .slice(-30);
    return {
      total_kwh, solar_kwh, grid_kwh, total_cost, total_duration_s,
      count, avg_session_kwh, solar_share, days,
    };
  }, [sessions]);

  const maxDayKwh = stats && stats.days.length > 0
    ? Math.max(...stats.days.map(([, d]) => d.solar + d.grid), 1)
    : 1;

  return (
    <div className="min-h-screen">
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        <h2 className="text-lg font-semibold">Ladevorgaenge</h2>

        {stats && (
          <>
            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="glass-panel rounded-2xl p-4">
                <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                  <Zap className="w-3.5 h-3.5" /> Gesamt
                </div>
                <div className="mono text-xl font-bold">{stats.total_kwh.toFixed(1)} kWh</div>
                <div className="text-xs text-muted-foreground mt-0.5">{stats.count} Sitzungen</div>
              </div>
              <div className="glass-panel rounded-2xl p-4">
                <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                  <Sun className="w-3.5 h-3.5 text-amber-500" /> Sonnenstrom
                </div>
                <div className="mono text-xl font-bold text-amber-500">{stats.solar_kwh.toFixed(1)} kWh</div>
                <div className="text-xs text-muted-foreground mt-0.5">{stats.solar_share.toFixed(0)}% Anteil</div>
              </div>
              <div className="glass-panel rounded-2xl p-4">
                <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                  <Plug2 className="w-3.5 h-3.5 text-red-400" /> Netzstrom
                </div>
                <div className="mono text-xl font-bold text-red-400">{stats.grid_kwh.toFixed(1)} kWh</div>
                <div className="text-xs text-muted-foreground mt-0.5">{(100 - stats.solar_share).toFixed(0)}% Anteil</div>
              </div>
              <div className="glass-panel rounded-2xl p-4">
                <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                  <Clock className="w-3.5 h-3.5" /> Ladezeit
                </div>
                <div className="mono text-xl font-bold">{formatDuration(stats.total_duration_s)}</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  Ø {stats.avg_session_kwh.toFixed(1)} kWh/Sitzung
                </div>
              </div>
            </div>

            {/* Solar/Grid Anteil Balken */}
            <div className="glass-panel rounded-2xl p-5">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="w-4 h-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">Verteilung Sonne / Netz</h3>
              </div>
              <div className="relative h-8 rounded-full overflow-hidden bg-muted flex">
                <div
                  className="bg-amber-500 flex items-center justify-center text-xs font-medium text-background transition-all"
                  style={{ width: `${stats.solar_share}%` }}
                >
                  {stats.solar_share > 15 ? `${stats.solar_share.toFixed(0)}% Sonne` : ""}
                </div>
                <div
                  className="bg-red-400 flex items-center justify-center text-xs font-medium text-background transition-all"
                  style={{ width: `${100 - stats.solar_share}%` }}
                >
                  {100 - stats.solar_share > 15 ? `${(100 - stats.solar_share).toFixed(0)}% Netz` : ""}
                </div>
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground mt-2">
                <span>{stats.solar_kwh.toFixed(1)} kWh aus PV/Batterie</span>
                <span>{stats.grid_kwh.toFixed(1)} kWh aus Netz</span>
              </div>
            </div>

            {/* Tages-Chart (letzte 30 Tage, gestapelt) */}
            {stats.days.length > 0 && (
              <div className="glass-panel rounded-2xl p-5">
                <div className="flex items-center gap-2 mb-3">
                  <BarChart3 className="w-4 h-4 text-muted-foreground" />
                  <h3 className="text-sm font-medium">Letzte 30 Tage</h3>
                </div>
                <div className="flex items-end gap-1 h-32">
                  {stats.days.map(([day, d]) => {
                    const total = d.solar + d.grid;
                    const pctSolar = (d.solar / maxDayKwh) * 100;
                    const pctGrid = (d.grid / maxDayKwh) * 100;
                    return (
                      <div
                        key={day}
                        className="flex-1 flex flex-col justify-end group relative"
                        title={`${day}: ${d.solar.toFixed(1)} kWh Solar + ${d.grid.toFixed(1)} kWh Netz = ${total.toFixed(1)} kWh`}
                      >
                        <div className="bg-red-400 transition-all" style={{ height: `${pctGrid}%` }} />
                        <div className="bg-amber-500 transition-all" style={{ height: `${pctSolar}%` }} />
                        <div className="invisible group-hover:visible absolute -top-12 left-1/2 -translate-x-1/2 bg-background border border-border rounded px-2 py-1 text-xs whitespace-nowrap z-10">
                          <div className="font-medium">{day}</div>
                          <div className="text-amber-500">Solar: {d.solar.toFixed(1)} kWh</div>
                          <div className="text-red-400">Netz: {d.grid.toFixed(1)} kWh</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="flex items-center justify-between text-xs text-muted-foreground mt-2">
                  <span>{stats.days[0]?.[0]}</span>
                  <span>{stats.days[stats.days.length - 1]?.[0]}</span>
                </div>
              </div>
            )}
          </>
        )}

        {/* Session-Liste */}
        <div className="glass-panel rounded-2xl p-5">
          <h3 className="text-sm font-medium mb-3">Alle Ladevorgaenge</h3>
          {sessions.length === 0 ? (
            <p className="text-muted-foreground text-sm">Noch keine Ladevorgaenge vorhanden.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/60 text-left text-muted-foreground">
                    <th className="py-2 pr-4">Beginn</th>
                    <th className="py-2 pr-4">Dauer</th>
                    <th className="py-2 pr-4">Ladepunkt</th>
                    <th className="py-2 pr-4">Modus</th>
                    <th className="py-2 pr-4">Energie</th>
                    <th className="py-2 pr-4 text-amber-500">Sonne</th>
                    <th className="py-2 pr-4 text-red-400">Netz</th>
                    <th className="py-2 pr-4">SoC</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map(s => {
                    const dur = durationFromStrings(s.started_at, s.finished_at);
                    const grid = s.grid_kwh ?? Math.max(0, (s.energy_kwh || 0) - (s.solar_kwh || 0));
                    const solarPct = s.energy_kwh > 0 ? (s.solar_kwh / s.energy_kwh) * 100 : 0;
                    return (
                      <tr key={s.id} className="border-b border-border/40 hover:bg-muted/30 transition-colors">
                        <td className="py-2 pr-4 mono text-xs whitespace-nowrap">
                          {new Date(s.started_at + "Z").toLocaleString("de")}
                        </td>
                        <td className="py-2 pr-4 mono text-xs text-muted-foreground">{formatDuration(dur)}</td>
                        <td className="py-2 pr-4">{s.loadpoint}</td>
                        <td className="py-2 pr-4 text-muted-foreground">{MODE_LABEL[s.mode || ""] || s.mode || "---"}</td>
                        <td className="py-2 pr-4 mono">{s.energy_kwh.toFixed(1)}</td>
                        <td className="py-2 pr-4 mono text-amber-500">{s.solar_kwh.toFixed(1)} ({solarPct.toFixed(0)}%)</td>
                        <td className="py-2 pr-4 mono text-red-400">{grid.toFixed(1)}</td>
                        <td className="py-2 pr-4 mono text-xs text-muted-foreground">
                          {s.vehicle_soc_start != null && s.vehicle_soc_end != null
                            ? `${Math.round(s.vehicle_soc_start)}→${Math.round(s.vehicle_soc_end)}%`
                            : "---"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

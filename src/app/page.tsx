"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import Header from "@/components/dashboard/Header";
import EnergyFlow from "@/components/dashboard/EnergyFlow";
import LoadpointCard from "@/components/dashboard/LoadpointCard";
import TelemetryChart from "@/components/dashboard/TelemetryChart";

interface SiteState {
  site_name?: string;
  grid_w: number;
  pv_w: number;
  battery_w: number;
  battery_soc: number;
  consumption_w: number;
  loadpoints: Array<{
    name: string;
    mode: string;
    status: string;
    power_w?: number;
    charging_power_w?: number;
    current_a?: number;
    target_current_a?: number;
    phases: number;
    session_energy_kwh?: number;
    energy_kwh?: number;
    vehicle?: string;
    vehicle_soc?: number | null;
    target_soc?: number;
    min_soc?: number;
    battery_kwh?: number;
  }>;
  updated_at?: string;
}

const EMPTY_STATE: SiteState = {
  grid_w: 0, pv_w: 0, battery_w: 0, battery_soc: 0, consumption_w: 0,
  loadpoints: [],
};

export default function Dashboard() {
  const router = useRouter();
  const [configChecked, setConfigChecked] = useState(false);
  const [state, setState] = useState<SiteState>(EMPTY_STATE);
  const [connected, setConnected] = useState(false);

  // Check if system is configured; redirect to /setup if not
  useEffect(() => {
    fetch("/api/config/status")
      .then(r => r.json())
      .then(data => {
        if (data.configured === false) {
          router.push("/setup");
        } else {
          setConfigChecked(true);
        }
      })
      .catch(() => setConfigChecked(true)); // on error, show dashboard anyway
  }, [router]);

  // SSE connection for real-time updates
  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimeout: NodeJS.Timeout;

    function connect() {
      es = new EventSource("/api/events");
      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setState(data);
          setConnected(true);
        } catch { /* ignore parse errors */ }
      };
      es.onerror = () => {
        es?.close();
        setConnected(false);
        retryTimeout = setTimeout(connect, 5000);
      };
    }

    connect();
    return () => {
      es?.close();
      clearTimeout(retryTimeout);
    };
  }, []);

  // Fallback: poll every 10s if SSE fails
  useEffect(() => {
    if (connected) return;
    const interval = setInterval(() => {
      fetch("/api/state")
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data && !data.status) setState(data); })
        .catch(() => {});
    }, 10000);
    return () => clearInterval(interval);
  }, [connected]);

  const handleModeChange = useCallback((loadpointName: string, mode: string) => {
    // Optimistisches Update — Button reagiert sofort
    setState(prev => ({
      ...prev,
      loadpoints: prev.loadpoints.map(lp =>
        lp.name === loadpointName ? { ...lp, mode } : lp
      ),
    }));
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "set_mode", loadpoint: loadpointName, mode }),
    });
  }, []);

  const timeSince = (() => {
    if (!state.updated_at) return "---";
    const ts = state.updated_at.endsWith("Z") || state.updated_at.includes("+")
      ? new Date(state.updated_at)
      : new Date(state.updated_at + "Z");
    const secs = Math.round((Date.now() - ts.getTime()) / 1000);
    if (isNaN(secs) || secs < 0) return "jetzt";
    if (secs < 60) return `${secs}s`;
    return `${Math.round(secs / 60)}m`;
  })();

  if (!configChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin h-8 w-8 border-2 border-current border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        {/* Status bar */}
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{state.site_name || "Wald EMS"}</span>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${connected ? "bg-status-online glow-dot" : "bg-status-offline"}`} />
            <span>Aktualisiert vor {timeSince}</span>
          </div>
        </div>

        {/* Energy flow */}
        <EnergyFlow
          grid_w={state.grid_w}
          pv_w={state.pv_w}
          battery_w={state.battery_w}
          battery_soc={state.battery_soc}
          consumption_w={state.consumption_w}
        />

        {/* Loadpoints */}
        {state.loadpoints.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {state.loadpoints.map(lp => (
              <LoadpointCard
                key={lp.name}
                name={lp.name}
                mode={lp.mode || "off"}
                status={lp.status || "disconnected"}
                power_w={lp.power_w || lp.charging_power_w || 0}
                current_a={lp.current_a || lp.target_current_a || 0}
                phases={lp.phases || 1}
                energy_kwh={lp.session_energy_kwh || lp.energy_kwh || 0}
                vehicle={lp.vehicle}
                vehicle_soc={lp.vehicle_soc ?? undefined}
                target_soc={lp.target_soc}
                min_soc={lp.min_soc}
                battery_kwh={lp.battery_kwh}
                onModeChange={(mode) => handleModeChange(lp.name, mode)}
              />
            ))}
          </div>
        )}

        {/* Chart */}
        <TelemetryChart />

        {/* KPI row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <KPICard label="PV heute" value={`${(state.pv_w / 1000).toFixed(1)} kW`} />
          <KPICard label="Netzbezug" value={`${(Math.max(0, state.grid_w) / 1000).toFixed(1)} kW`} />
          <KPICard label="Einspeisung" value={`${(Math.abs(Math.min(0, state.grid_w)) / 1000).toFixed(1)} kW`} />
          <KPICard label="Batterie" value={`${state.battery_soc}%`} />
        </div>
      </main>
    </div>
  );
}

function KPICard({ label, value }: { label: string; value: string }) {
  return (
    <div className="glass-panel rounded-xl p-4 text-center">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className="mono text-lg font-semibold">{value}</p>
    </div>
  );
}

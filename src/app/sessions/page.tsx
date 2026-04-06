"use client";

import { useState, useEffect } from "react";
import Header from "@/components/dashboard/Header";

interface Session {
  id: number;
  loadpoint: string;
  started_at: string;
  finished_at: string | null;
  energy_kwh: number;
  solar_kwh: number;
  max_power_w: number;
  avg_power_w: number;
  mode: string | null;
  vehicle: string | null;
  cost_eur: number;
}

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);

  useEffect(() => {
    fetch("/api/sessions?limit=100")
      .then(r => r.json())
      .then(setSessions)
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen">
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6">
        <h2 className="text-lg font-semibold mb-4">Ladevorgaenge</h2>
        {sessions.length === 0 ? (
          <p className="text-muted-foreground text-sm">Noch keine Ladevorgaenge vorhanden.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 pr-4">Beginn</th>
                  <th className="py-2 pr-4">Ladepunkt</th>
                  <th className="py-2 pr-4">Fahrzeug</th>
                  <th className="py-2 pr-4">Energie</th>
                  <th className="py-2 pr-4">Solar</th>
                  <th className="py-2 pr-4">Modus</th>
                  <th className="py-2 pr-4">Kosten</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map(s => (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                    <td className="py-2 pr-4 mono text-xs">
                      {new Date(s.started_at + "Z").toLocaleString("de")}
                    </td>
                    <td className="py-2 pr-4">{s.loadpoint}</td>
                    <td className="py-2 pr-4 text-muted-foreground">{s.vehicle || "---"}</td>
                    <td className="py-2 pr-4 mono">{s.energy_kwh.toFixed(1)} kWh</td>
                    <td className="py-2 pr-4 mono text-primary">
                      {s.energy_kwh > 0 ? `${Math.round((s.solar_kwh / s.energy_kwh) * 100)}%` : "---"}
                    </td>
                    <td className="py-2 pr-4">{s.mode || "---"}</td>
                    <td className="py-2 pr-4 mono">{s.cost_eur.toFixed(2)} EUR</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}

"use client";

import { useState, useEffect } from "react";
import Header from "@/components/dashboard/Header";
import { RefreshCw, Save } from "lucide-react";

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [logs, setLogs] = useState<Array<{ id: number; level: string; source: string; message: string; created_at: string }>>([]);
  const [saving, setSaving] = useState(false);
  const [yamlText, setYamlText] = useState("");

  useEffect(() => {
    fetch("/api/config").then(r => r.json()).then(c => {
      setConfig(c);
      setYamlText(JSON.stringify(c, null, 2));
    }).catch(() => {});

    fetch("/api/logs?limit=50").then(r => r.json()).then(setLogs).catch(() => {});
  }, []);

  function refreshLogs() {
    fetch("/api/logs?limit=50").then(r => r.json()).then(setLogs).catch(() => {});
  }

  function sendCommand(action: string) {
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  }

  return (
    <div className="min-h-screen">
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        <h2 className="text-lg font-semibold">Einstellungen</h2>

        {/* Quick actions */}
        <div className="glass-panel rounded-2xl p-5">
          <h3 className="text-sm font-medium text-muted-foreground mb-3">Befehle</h3>
          <div className="flex gap-2 flex-wrap">
            <button onClick={() => sendCommand("restart_client")} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg text-sm transition-colors">
              Client neu starten
            </button>
            <button onClick={() => sendCommand("reload_config")} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg text-sm transition-colors">
              Config neu laden
            </button>
            <button onClick={() => sendCommand("cleanup_db")} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg text-sm transition-colors">
              Datenbank bereinigen
            </button>
          </div>
        </div>

        {/* Config display */}
        {config && (
          <div className="glass-panel rounded-2xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-muted-foreground">Konfiguration (wald-ems.yaml)</h3>
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              Bearbeite die Datei <code className="mono bg-muted px-1 rounded">wald-ems.yaml</code> direkt auf dem Raspberry Pi.
            </p>
            <pre className="bg-muted/50 rounded-xl p-4 text-xs mono overflow-x-auto max-h-96">
              {JSON.stringify(config, null, 2)}
            </pre>
          </div>
        )}

        {/* Logs */}
        <div className="glass-panel rounded-2xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-muted-foreground">Logs</h3>
            <button onClick={refreshLogs} className="p-1.5 hover:bg-muted rounded-lg transition-colors">
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
          <div className="space-y-1 max-h-96 overflow-y-auto">
            {logs.length === 0 ? (
              <p className="text-sm text-muted-foreground">Keine Logs vorhanden.</p>
            ) : logs.map(log => (
              <div key={log.id} className="flex items-start gap-2 text-xs py-1 border-b border-border/30">
                <span className={`mono shrink-0 ${
                  log.level === "error" ? "text-destructive" :
                  log.level === "warning" ? "text-status-warning" :
                  "text-muted-foreground"
                }`}>
                  {log.level.toUpperCase().padEnd(5)}
                </span>
                <span className="mono text-muted-foreground shrink-0">
                  {new Date(log.created_at + "Z").toLocaleTimeString("de")}
                </span>
                <span className="text-muted-foreground">[{log.source}]</span>
                <span>{log.message}</span>
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}

"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/dashboard/Header";
import { RefreshCw, Save, Download, CheckCircle, AlertCircle, Loader2 } from "lucide-react";

interface UpdateInfo {
  current_commit: string;
  remote_commit: string;
  current_date: string;
  behind: number;
  update_available: boolean;
  client_version: string;
  error?: string;
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [logs, setLogs] = useState<Array<{ id: number; level: string; source: string; message: string; created_at: string }>>([]);
  const [saving, setSaving] = useState(false);
  const [yamlText, setYamlText] = useState("");

  // Update state
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [updateChecking, setUpdateChecking] = useState(false);
  const [updateRunning, setUpdateRunning] = useState(false);
  const [updateMessage, setUpdateMessage] = useState("");

  useEffect(() => {
    fetch("/api/config").then(r => r.json()).then(c => {
      setConfig(c);
      setYamlText(JSON.stringify(c, null, 2));
    }).catch(() => {});

    fetch("/api/logs?limit=50").then(r => r.json()).then(setLogs).catch(() => {});

    // Update-Check beim Laden
    checkForUpdate();
  }, []);

  const checkForUpdate = useCallback(() => {
    setUpdateChecking(true);
    setUpdateMessage("");
    fetch("/api/update")
      .then(r => r.json())
      .then(info => {
        setUpdateInfo(info);
        setUpdateChecking(false);
      })
      .catch(() => {
        setUpdateChecking(false);
        setUpdateMessage("Update-Check fehlgeschlagen");
      });
  }, []);

  function runUpdate() {
    setUpdateRunning(true);
    setUpdateMessage("Update wird ausgefuehrt...");
    fetch("/api/update", { method: "POST" })
      .then(r => r.json())
      .then(res => {
        if (res.ok) {
          setUpdateMessage("Update laeuft — Seite wird in 30s neu geladen...");
          // Auto-reload nach 30s (Services restarten)
          setTimeout(() => window.location.reload(), 30000);
        } else {
          setUpdateMessage(`Fehler: ${res.error}`);
          setUpdateRunning(false);
        }
      })
      .catch(() => {
        setUpdateMessage("Update-Request fehlgeschlagen");
        setUpdateRunning(false);
      });
  }

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

        {/* Update Panel */}
        <div className="glass-panel rounded-2xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-muted-foreground">Software-Update</h3>
            <button
              onClick={checkForUpdate}
              disabled={updateChecking}
              className="p-1.5 hover:bg-muted rounded-lg transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${updateChecking ? "animate-spin" : ""}`} />
            </button>
          </div>

          {updateInfo && (
            <div className="space-y-3">
              {/* Version info */}
              <div className="flex items-center gap-4 text-sm">
                <div>
                  <span className="text-muted-foreground">Aktuell: </span>
                  <span className="mono font-medium">{updateInfo.current_commit}</span>
                </div>
                {updateInfo.client_version !== "unknown" && (
                  <div>
                    <span className="text-muted-foreground">Client: </span>
                    <span className="mono font-medium">v{updateInfo.client_version}</span>
                  </div>
                )}
                <div className="text-xs text-muted-foreground">
                  {updateInfo.current_date}
                </div>
              </div>

              {/* Update status */}
              {updateInfo.update_available ? (
                <div className="flex items-center justify-between p-3 rounded-xl bg-primary/10 border border-primary/20">
                  <div className="flex items-center gap-2">
                    <Download className="w-4 h-4 text-primary" />
                    <span className="text-sm font-medium">
                      Update verfuegbar — {updateInfo.behind} {updateInfo.behind === 1 ? "Commit" : "Commits"} hinter origin/main
                    </span>
                  </div>
                  <button
                    onClick={runUpdate}
                    disabled={updateRunning}
                    className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                  >
                    {updateRunning ? (
                      <><Loader2 className="w-4 h-4 animate-spin" /> Aktualisiere...</>
                    ) : (
                      <><Download className="w-4 h-4" /> Jetzt aktualisieren</>
                    )}
                  </button>
                </div>
              ) : updateInfo.error ? (
                <div className="flex items-center gap-2 p-3 rounded-xl bg-destructive/10 border border-destructive/20">
                  <AlertCircle className="w-4 h-4 text-destructive" />
                  <span className="text-sm">{updateInfo.error}</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 p-3 rounded-xl bg-status-online/10 border border-status-online/20">
                  <CheckCircle className="w-4 h-4 text-status-online" />
                  <span className="text-sm">System ist aktuell</span>
                </div>
              )}

              {/* Update message */}
              {updateMessage && (
                <p className="text-sm text-muted-foreground">{updateMessage}</p>
              )}
            </div>
          )}

          {updateChecking && !updateInfo && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              Pruefe auf Updates...
            </div>
          )}
        </div>

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

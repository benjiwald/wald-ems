"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/dashboard/Header";
import {
  RefreshCw, Save, Download, CheckCircle, AlertCircle, Loader2,
  Plus, Trash2, Pencil, X, ChevronRight, Gauge, Plug, MapPin,
  Car, Settings, Cpu, Zap,
} from "lucide-react";
import {
  METER_TEMPLATES, CHARGER_TEMPLATES, CHARGING_MODES,
  type MeterTemplate, type ChargerTemplate, type FieldDef,
} from "@/lib/device-templates";
import type { WaldConfig } from "@/lib/config";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UpdateInfo {
  current_commit: string;
  remote_commit: string;
  current_date: string;
  behind: number;
  update_available: boolean;
  client_version: string;
  error?: string;
}

type TabId = "allgemein" | "messgeraete" | "wallboxen" | "ladepunkte" | "fahrzeuge" | "system";

interface Tab {
  id: TabId;
  label: string;
  icon: React.ReactNode;
}

const TABS: Tab[] = [
  { id: "allgemein",   label: "Allgemein",   icon: <Settings className="w-4 h-4" /> },
  { id: "messgeraete", label: "Messgeraete", icon: <Gauge className="w-4 h-4" /> },
  { id: "wallboxen",   label: "Wallboxen",   icon: <Plug className="w-4 h-4" /> },
  { id: "ladepunkte",  label: "Ladepunkte",  icon: <MapPin className="w-4 h-4" /> },
  { id: "fahrzeuge",   label: "Fahrzeuge",   icon: <Car className="w-4 h-4" /> },
  { id: "system",      label: "System",      icon: <Cpu className="w-4 h-4" /> },
];

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

function Toast({ message, type, onClose }: { message: string; type: "success" | "error"; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-xl shadow-lg text-sm font-medium
      ${type === "success"
        ? "bg-status-online/15 border border-status-online/30 text-status-online"
        : "bg-destructive/15 border border-destructive/30 text-destructive"
      }`}
    >
      {type === "success" ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
      {message}
      <button onClick={onClose} className="ml-2 p-0.5 hover:opacity-70"><X className="w-3.5 h-3.5" /></button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confirmation dialog
// ---------------------------------------------------------------------------

function ConfirmDialog({ message, onConfirm, onCancel }: { message: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="glass-panel rounded-2xl p-6 max-w-sm mx-4 space-y-4">
        <p className="text-sm">{message}</p>
        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-muted hover:bg-muted/80 transition-colors">
            Abbrechen
          </button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm rounded-lg bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors">
            Loeschen
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared form field renderer
// ---------------------------------------------------------------------------

function FormField({ field, value, onChange }: { field: FieldDef; value: any; onChange: (v: any) => void }) {
  const base = "w-full rounded-lg bg-muted/50 border border-border/60 px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/40 transition-colors";

  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
        {field.label}{field.required && <span className="text-destructive ml-0.5">*</span>}
      </label>
      {field.type === "select" ? (
        <select
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        >
          <option value="">Bitte waehlen...</option>
          {field.options?.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      ) : (
        <input
          type={field.type}
          value={value ?? ""}
          onChange={(e) => onChange(field.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
          placeholder={field.placeholder}
          min={field.min}
          max={field.max}
          step={field.step}
          className={base}
        />
      )}
      {field.help && <p className="text-xs text-muted-foreground/60 mt-1">{field.help}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Template picker
// ---------------------------------------------------------------------------

function TemplatePicker<T extends { type: string; label: string; description: string; icon: string }>({
  templates, onSelect, onCancel,
}: { templates: T[]; onSelect: (t: T) => void; onCancel: () => void }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-medium">Geraetetyp waehlen</h4>
        <button onClick={onCancel} className="p-1.5 hover:bg-muted rounded-lg transition-colors">
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {templates.map((t) => (
          <button
            key={t.type}
            onClick={() => onSelect(t)}
            className="flex items-start gap-3 p-3 rounded-xl bg-muted/40 hover:bg-muted/70 border border-border/40 hover:border-primary/30 transition-all text-left group"
          >
            <span className="text-xl mt-0.5">{t.icon}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium group-hover:text-primary transition-colors">{t.label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{t.description}</p>
            </div>
            <ChevronRight className="w-4 h-4 text-muted-foreground mt-1 shrink-0 group-hover:text-primary transition-colors" />
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const [config, setConfig] = useState<WaldConfig | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("allgemein");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ section: string; index: number } | null>(null);

  // System tab state
  const [logs, setLogs] = useState<Array<{ id: number; level: string; source: string; message: string; created_at: string }>>([]);
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [updateChecking, setUpdateChecking] = useState(false);
  const [updateRunning, setUpdateRunning] = useState(false);
  const [updateMessage, setUpdateMessage] = useState("");

  // Device editing state
  const [editingMeter, setEditingMeter] = useState<number | "new" | null>(null);
  const [editingCharger, setEditingCharger] = useState<number | "new" | null>(null);
  const [editingLoadpoint, setEditingLoadpoint] = useState<number | "new" | null>(null);
  const [editingVehicle, setEditingVehicle] = useState<number | "new" | null>(null);

  // Template picker state
  const [pickingMeterTemplate, setPickingMeterTemplate] = useState(false);
  const [pickingChargerTemplate, setPickingChargerTemplate] = useState(false);

  // Draft state for editing forms
  const [draft, setDraft] = useState<Record<string, any>>({});

  // -----------------------------------------------------------------------
  // Load config + logs
  // -----------------------------------------------------------------------

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((c: WaldConfig) => setConfig(c))
      .catch(() => {});
    refreshLogs();
    checkForUpdate();
  }, []);

  // -----------------------------------------------------------------------
  // Save config helper
  // -----------------------------------------------------------------------

  const saveConfig = useCallback(async (newConfig: WaldConfig) => {
    setSaving(true);
    try {
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newConfig),
      });
      if (!res.ok) throw new Error("Save failed");

      // Notify Python client
      await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "reload_config" }),
      });

      setConfig(newConfig);
      setToast({ message: "Konfiguration gespeichert", type: "success" });
    } catch {
      setToast({ message: "Fehler beim Speichern", type: "error" });
    } finally {
      setSaving(false);
    }
  }, []);

  // -----------------------------------------------------------------------
  // System tab helpers (preserved from original)
  // -----------------------------------------------------------------------

  const checkForUpdate = useCallback(() => {
    setUpdateChecking(true);
    setUpdateMessage("");
    fetch("/api/update")
      .then((r) => r.json())
      .then((info) => { setUpdateInfo(info); setUpdateChecking(false); })
      .catch(() => { setUpdateChecking(false); setUpdateMessage("Update-Check fehlgeschlagen"); });
  }, []);

  function runUpdate() {
    setUpdateRunning(true);
    setUpdateMessage("Update wird ausgefuehrt...");
    fetch("/api/update", { method: "POST" })
      .then((r) => r.json())
      .then((res) => {
        if (res.ok) {
          setUpdateMessage("Update laeuft — Seite wird in 30s neu geladen...");
          setTimeout(() => window.location.reload(), 30000);
        } else {
          setUpdateMessage(`Fehler: ${res.error}`);
          setUpdateRunning(false);
        }
      })
      .catch(() => { setUpdateMessage("Update-Request fehlgeschlagen"); setUpdateRunning(false); });
  }

  function refreshLogs() {
    fetch("/api/logs?limit=50").then((r) => r.json()).then(setLogs).catch(() => {});
  }

  function sendCommand(action: string) {
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  }

  // -----------------------------------------------------------------------
  // Generic device CRUD helpers
  // -----------------------------------------------------------------------

  function deleteDevice(section: "meters" | "chargers" | "loadpoints" | "vehicles", index: number) {
    if (!config) return;
    const updated = { ...config, [section]: config[section].filter((_: any, i: number) => i !== index) };
    saveConfig(updated);
    setConfirmDelete(null);
  }

  function startEditDevice(section: string, index: number, data: Record<string, any>) {
    setDraft({ ...data });
    if (section === "meters") setEditingMeter(index);
    if (section === "chargers") setEditingCharger(index);
    if (section === "loadpoints") setEditingLoadpoint(index);
    if (section === "vehicles") setEditingVehicle(index);
  }

  function saveDevice(section: "meters" | "chargers" | "loadpoints" | "vehicles", index: number | "new") {
    if (!config) return;
    const arr = [...config[section]] as any[];
    if (index === "new") {
      arr.push({ ...draft });
    } else {
      arr[index] = { ...draft };
    }
    const updated = { ...config, [section]: arr };
    saveConfig(updated);
    // Reset editing state
    setEditingMeter(null);
    setEditingCharger(null);
    setEditingLoadpoint(null);
    setEditingVehicle(null);
    setDraft({});
  }

  function cancelEdit() {
    setEditingMeter(null);
    setEditingCharger(null);
    setEditingLoadpoint(null);
    setEditingVehicle(null);
    setPickingMeterTemplate(false);
    setPickingChargerTemplate(false);
    setDraft({});
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  if (!config) {
    return (
      <div className="min-h-screen">
        <Header />
        <main className="max-w-5xl mx-auto px-4 py-6 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        <h2 className="text-lg font-semibold">Einstellungen</h2>

        {/* Tab bar */}
        <div className="overflow-x-auto -mx-4 px-4">
          <div className="flex gap-1 min-w-max">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all whitespace-nowrap
                  ${activeTab === tab.id
                    ? "bg-primary/15 text-primary border border-primary/25"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Tab content */}
        {activeTab === "allgemein" && <TabAllgemein config={config} saving={saving} onSave={saveConfig} />}
        {activeTab === "messgeraete" && (
          <TabDeviceList
            title="Messgeraete"
            description="Energiezaehler und Sensoren fuer die Messung von Netz, PV, Batterie und Verbrauch."
            items={config.meters}
            templates={METER_TEMPLATES}
            editing={editingMeter}
            pickingTemplate={pickingMeterTemplate}
            draft={draft}
            setDraft={setDraft}
            onAdd={() => setPickingMeterTemplate(true)}
            onPickTemplate={(t: MeterTemplate) => {
              setPickingMeterTemplate(false);
              setDraft({ name: "", type: t.type, ...t.defaults });
              setEditingMeter("new");
            }}
            onCancelPick={() => setPickingMeterTemplate(false)}
            onEdit={(i) => startEditDevice("meters", i, config.meters[i])}
            onDelete={(i) => setConfirmDelete({ section: "meters", index: i })}
            onSave={(i) => saveDevice("meters", i)}
            onCancel={cancelEdit}
            getTemplate={(type: string) => METER_TEMPLATES.find((t) => t.type === type)}
            renderCard={(item, i) => (
              <DeviceCard
                key={i}
                icon={METER_TEMPLATES.find((t) => t.type === item.type)?.icon ?? "📊"}
                name={item.name || "(Ohne Name)"}
                subtitle={METER_TEMPLATES.find((t) => t.type === item.type)?.label ?? item.type}
                detail={item.host ? `${item.host}:${item.port ?? ""}` : ""}
                onEdit={() => startEditDevice("meters", i, config.meters[i])}
                onDelete={() => setConfirmDelete({ section: "meters", index: i })}
              />
            )}
          />
        )}
        {activeTab === "wallboxen" && (
          <TabDeviceList
            title="Wallboxen"
            description="Ladestationen / Wallboxen fuer Elektrofahrzeuge."
            items={config.chargers}
            templates={CHARGER_TEMPLATES}
            editing={editingCharger}
            pickingTemplate={pickingChargerTemplate}
            draft={draft}
            setDraft={setDraft}
            onAdd={() => setPickingChargerTemplate(true)}
            onPickTemplate={(t: ChargerTemplate) => {
              setPickingChargerTemplate(false);
              setDraft({ name: "", type: t.type, ...t.defaults });
              setEditingCharger("new");
            }}
            onCancelPick={() => setPickingChargerTemplate(false)}
            onEdit={(i) => startEditDevice("chargers", i, config.chargers[i])}
            onDelete={(i) => setConfirmDelete({ section: "chargers", index: i })}
            onSave={(i) => saveDevice("chargers", i)}
            onCancel={cancelEdit}
            getTemplate={(type: string) => CHARGER_TEMPLATES.find((t) => t.type === type)}
            renderCard={(item, i) => (
              <DeviceCard
                key={i}
                icon={CHARGER_TEMPLATES.find((t) => t.type === item.type)?.icon ?? "🔌"}
                name={item.name || "(Ohne Name)"}
                subtitle={CHARGER_TEMPLATES.find((t) => t.type === item.type)?.label ?? item.type}
                detail={item.host ? `${item.host}:${item.port ?? ""}` : ""}
                onEdit={() => startEditDevice("chargers", i, config.chargers[i])}
                onDelete={() => setConfirmDelete({ section: "chargers", index: i })}
              />
            )}
          />
        )}
        {activeTab === "ladepunkte" && (
          <TabLadepunkte
            config={config}
            editing={editingLoadpoint}
            draft={draft}
            setDraft={setDraft}
            onAdd={() => {
              setDraft({ name: "", charger: "", meter: "", mode: "pv", min_current: 6, max_current: 16, phases: 3 });
              setEditingLoadpoint("new");
            }}
            onEdit={(i) => startEditDevice("loadpoints", i, config.loadpoints[i])}
            onDelete={(i) => setConfirmDelete({ section: "loadpoints", index: i })}
            onSave={(i) => saveDevice("loadpoints", i)}
            onCancel={cancelEdit}
          />
        )}
        {activeTab === "fahrzeuge" && (
          <TabFahrzeuge
            config={config}
            editing={editingVehicle}
            draft={draft}
            setDraft={setDraft}
            onAdd={() => {
              setDraft({ name: "", manufacturer: "", battery_kwh: 60, vin: "", loadpoint: "" });
              setEditingVehicle("new");
            }}
            onEdit={(i) => startEditDevice("vehicles", i, config.vehicles[i])}
            onDelete={(i) => setConfirmDelete({ section: "vehicles", index: i })}
            onSave={(i) => saveDevice("vehicles", i)}
            onCancel={cancelEdit}
          />
        )}
        {activeTab === "system" && (
          <TabSystem
            updateInfo={updateInfo}
            updateChecking={updateChecking}
            updateRunning={updateRunning}
            updateMessage={updateMessage}
            logs={logs}
            onCheckUpdate={checkForUpdate}
            onRunUpdate={runUpdate}
            onRefreshLogs={refreshLogs}
            onSendCommand={sendCommand}
          />
        )}
      </main>

      {/* Toast */}
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

      {/* Confirm delete dialog */}
      {confirmDelete && (
        <ConfirmDialog
          message="Moechten Sie diesen Eintrag wirklich loeschen?"
          onConfirm={() => deleteDevice(confirmDelete.section as any, confirmDelete.index)}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
    </div>
  );
}

// ===========================================================================
// TAB: Allgemein
// ===========================================================================

function TabAllgemein({ config, saving, onSave }: { config: WaldConfig; saving: boolean; onSave: (c: WaldConfig) => void }) {
  const [site, setSite] = useState(config.site);

  useEffect(() => { setSite(config.site); }, [config.site]);

  function handleSave() {
    onSave({ ...config, site });
  }

  const inputClass = "w-full rounded-lg bg-muted/50 border border-border/60 px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/40 transition-colors";

  return (
    <div className="glass-panel rounded-2xl p-5 space-y-5">
      <div>
        <h3 className="text-sm font-medium mb-1">Standort-Einstellungen</h3>
        <p className="text-xs text-muted-foreground">Allgemeine Einstellungen fuer Ihren Standort.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Standortname</label>
          <input type="text" value={site.name} onChange={(e) => setSite({ ...site, name: e.target.value })} className={inputClass} placeholder="Mein Zuhause" />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Netzanschlussleistung (kW)</label>
          <input type="number" value={site.grid_limit_kw} onChange={(e) => setSite({ ...site, grid_limit_kw: Number(e.target.value) })} className={inputClass} min={0} step={0.5} />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Puffer (W)</label>
          <input type="number" value={site.buffer_w} onChange={(e) => setSite({ ...site, buffer_w: Number(e.target.value) })} className={inputClass} min={0} step={10} />
          <p className="text-xs text-muted-foreground/60 mt-1">Sicherheitspuffer zum Netzlimit</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Prioritaets-SoC (%)</label>
          <input type="number" value={site.priority_soc} onChange={(e) => setSite({ ...site, priority_soc: Number(e.target.value) })} className={inputClass} min={0} max={100} step={1} />
          <p className="text-xs text-muted-foreground/60 mt-1">Batterie hat Vorrang bis zu diesem Ladestand</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Strompreis (EUR/kWh)</label>
          <input type="number" value={site.grid_price_eur_kwh} onChange={(e) => setSite({ ...site, grid_price_eur_kwh: Number(e.target.value) })} className={inputClass} min={0} step={0.01} />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Einspeiseverguetung (EUR/kWh)</label>
          <input type="number" value={site.feedin_price_eur_kwh} onChange={(e) => setSite({ ...site, feedin_price_eur_kwh: Number(e.target.value) })} className={inputClass} min={0} step={0.001} />
        </div>
      </div>

      <div className="flex justify-end pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground rounded-xl text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Speichern
        </button>
      </div>
    </div>
  );
}

// ===========================================================================
// TAB: Generic device list (Meters / Chargers)
// ===========================================================================

function DeviceCard({ icon, name, subtitle, detail, onEdit, onDelete }: {
  icon: string; name: string; subtitle: string; detail: string;
  onEdit: () => void; onDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-4 p-4 rounded-xl bg-muted/30 border border-border/40 group card-hover">
      <span className="text-2xl">{icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{name}</p>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
        {detail && <p className="text-xs text-muted-foreground/60 mono mt-0.5">{detail}</p>}
      </div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button onClick={onEdit} className="p-2 hover:bg-muted rounded-lg transition-colors" title="Bearbeiten">
          <Pencil className="w-4 h-4 text-muted-foreground" />
        </button>
        <button onClick={onDelete} className="p-2 hover:bg-destructive/10 rounded-lg transition-colors" title="Loeschen">
          <Trash2 className="w-4 h-4 text-destructive/70" />
        </button>
      </div>
    </div>
  );
}

function TabDeviceList({
  title, description, items, templates, editing, pickingTemplate,
  draft, setDraft, onAdd, onPickTemplate, onCancelPick, onEdit, onDelete, onSave, onCancel,
  getTemplate, renderCard,
}: {
  title: string;
  description: string;
  items: any[];
  templates: (MeterTemplate | ChargerTemplate)[];
  editing: number | "new" | null;
  pickingTemplate: boolean;
  draft: Record<string, any>;
  setDraft: (d: Record<string, any>) => void;
  onAdd: () => void;
  onPickTemplate: (t: any) => void;
  onCancelPick: () => void;
  onEdit: (i: number) => void;
  onDelete: (i: number) => void;
  onSave: (i: number | "new") => void;
  onCancel: () => void;
  getTemplate: (type: string) => (MeterTemplate | ChargerTemplate) | undefined;
  renderCard: (item: any, i: number) => React.ReactNode;
}) {
  const template = draft.type ? getTemplate(draft.type) : undefined;

  return (
    <div className="space-y-4">
      <div className="glass-panel rounded-2xl p-5">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-medium">{title}</h3>
          {!pickingTemplate && editing === null && (
            <button onClick={onAdd} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-primary hover:bg-primary/10 rounded-lg transition-colors">
              <Plus className="w-4 h-4" /> Hinzufuegen
            </button>
          )}
        </div>
        <p className="text-xs text-muted-foreground mb-4">{description}</p>

        {/* Template picker */}
        {pickingTemplate && (
          <TemplatePicker templates={templates} onSelect={onPickTemplate} onCancel={onCancelPick} />
        )}

        {/* Editing form */}
        {editing !== null && !pickingTemplate && template && (
          <DeviceForm
            template={template}
            draft={draft}
            setDraft={setDraft}
            onSave={() => onSave(editing)}
            onCancel={onCancel}
            isNew={editing === "new"}
          />
        )}

        {/* Editing form for devices without a matching template */}
        {editing !== null && !pickingTemplate && !template && draft.type && (
          <DeviceFormGeneric
            draft={draft}
            setDraft={setDraft}
            onSave={() => onSave(editing)}
            onCancel={onCancel}
            isNew={editing === "new"}
          />
        )}

        {/* Device list */}
        {editing === null && !pickingTemplate && (
          <div className="space-y-2">
            {items.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">Noch keine {title.toLowerCase()} konfiguriert.</p>
            ) : (
              items.map((item, i) => renderCard(item, i))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function DeviceForm({ template, draft, setDraft, onSave, onCancel, isNew }: {
  template: MeterTemplate | ChargerTemplate;
  draft: Record<string, any>;
  setDraft: (d: Record<string, any>) => void;
  onSave: () => void;
  onCancel: () => void;
  isNew: boolean;
}) {
  const inputClass = "w-full rounded-lg bg-muted/50 border border-border/60 px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/40 transition-colors";

  return (
    <div className="space-y-4 p-4 rounded-xl bg-muted/20 border border-border/40">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-lg">{template.icon}</span>
        <h4 className="text-sm font-medium">{isNew ? "Neues Geraet" : "Bearbeiten"}: {template.label}</h4>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Name field (always first) */}
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">
            Name<span className="text-destructive ml-0.5">*</span>
          </label>
          <input
            type="text"
            value={draft.name ?? ""}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className={inputClass}
            placeholder="z.B. Victron System"
          />
        </div>

        {/* Template fields */}
        {template.fields.map((field) => (
          <FormField
            key={field.key}
            field={field}
            value={draft[field.key]}
            onChange={(v) => setDraft({ ...draft, [field.key]: v })}
          />
        ))}
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-muted hover:bg-muted/80 transition-colors">
          Abbrechen
        </button>
        <button
          onClick={onSave}
          disabled={!draft.name}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {isNew ? "Hinzufuegen" : "Speichern"}
        </button>
      </div>
    </div>
  );
}

function DeviceFormGeneric({ draft, setDraft, onSave, onCancel, isNew }: {
  draft: Record<string, any>;
  setDraft: (d: Record<string, any>) => void;
  onSave: () => void;
  onCancel: () => void;
  isNew: boolean;
}) {
  const inputClass = "w-full rounded-lg bg-muted/50 border border-border/60 px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/40 transition-colors";
  const fields = Object.keys(draft).filter((k) => k !== "type");

  return (
    <div className="space-y-4 p-4 rounded-xl bg-muted/20 border border-border/40">
      <h4 className="text-sm font-medium">{isNew ? "Neues Geraet" : "Bearbeiten"} ({draft.type})</h4>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {fields.map((key) => (
          <div key={key}>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">{key}</label>
            <input
              type={typeof draft[key] === "number" ? "number" : "text"}
              value={draft[key] ?? ""}
              onChange={(e) => setDraft({ ...draft, [key]: typeof draft[key] === "number" ? Number(e.target.value) : e.target.value })}
              className={inputClass}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-end gap-2 pt-2">
        <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-muted hover:bg-muted/80 transition-colors">
          Abbrechen
        </button>
        <button onClick={onSave} className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          <Save className="w-4 h-4" />
          {isNew ? "Hinzufuegen" : "Speichern"}
        </button>
      </div>
    </div>
  );
}

// ===========================================================================
// TAB: Ladepunkte
// ===========================================================================

function TabLadepunkte({ config, editing, draft, setDraft, onAdd, onEdit, onDelete, onSave, onCancel }: {
  config: WaldConfig;
  editing: number | "new" | null;
  draft: Record<string, any>;
  setDraft: (d: Record<string, any>) => void;
  onAdd: () => void;
  onEdit: (i: number) => void;
  onDelete: (i: number) => void;
  onSave: (i: number | "new") => void;
  onCancel: () => void;
}) {
  const inputClass = "w-full rounded-lg bg-muted/50 border border-border/60 px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/40 transition-colors";

  return (
    <div className="glass-panel rounded-2xl p-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-medium">Ladepunkte</h3>
        {editing === null && (
          <button onClick={onAdd} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-primary hover:bg-primary/10 rounded-lg transition-colors">
            <Plus className="w-4 h-4" /> Hinzufuegen
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground mb-4">Ladepunkte verbinden eine Wallbox mit Lademodus und Stromlimits.</p>

      {/* Editing form */}
      {editing !== null && (
        <div className="space-y-4 p-4 rounded-xl bg-muted/20 border border-border/40">
          <h4 className="text-sm font-medium">{editing === "new" ? "Neuer Ladepunkt" : "Ladepunkt bearbeiten"}</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Name<span className="text-destructive ml-0.5">*</span></label>
              <input type="text" value={draft.name ?? ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={inputClass} placeholder="z.B. Garage" />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Wallbox<span className="text-destructive ml-0.5">*</span></label>
              <select value={draft.charger ?? ""} onChange={(e) => setDraft({ ...draft, charger: e.target.value })} className={inputClass}>
                <option value="">Bitte waehlen...</option>
                {config.chargers.map((c) => (
                  <option key={c.name} value={c.name}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Messgeraet (optional)</label>
              <select value={draft.meter ?? ""} onChange={(e) => setDraft({ ...draft, meter: e.target.value })} className={inputClass}>
                <option value="">Keines</option>
                {config.meters.map((m) => (
                  <option key={m.name} value={m.name}>{m.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Lademodus</label>
              <select value={draft.mode ?? "pv"} onChange={(e) => setDraft({ ...draft, mode: e.target.value })} className={inputClass}>
                {CHARGING_MODES.map((m) => (
                  <option key={m.mode} value={m.mode}>{m.icon} {m.label} — {m.description}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Min. Strom (A)</label>
              <input type="number" value={draft.min_current ?? 6} onChange={(e) => setDraft({ ...draft, min_current: Number(e.target.value) })} className={inputClass} min={6} max={32} step={1} />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Max. Strom (A)</label>
              <input type="number" value={draft.max_current ?? 16} onChange={(e) => setDraft({ ...draft, max_current: Number(e.target.value) })} className={inputClass} min={6} max={32} step={1} />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Phasen</label>
              <select value={draft.phases ?? 3} onChange={(e) => setDraft({ ...draft, phases: Number(e.target.value) })} className={inputClass}>
                <option value={1}>1-phasig</option>
                <option value={3}>3-phasig</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Ziel-SoC (%)</label>
              <input type="number" value={draft.target_soc ?? 100} onChange={(e) => setDraft({ ...draft, target_soc: Number(e.target.value) })} className={inputClass} min={10} max={100} step={5} />
              <p className="text-[10px] text-muted-foreground mt-1">Laden stoppt bei diesem SoC</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Min-SoC (%)</label>
              <input type="number" value={draft.min_soc ?? 0} onChange={(e) => setDraft({ ...draft, min_soc: Number(e.target.value) })} className={inputClass} min={0} max={100} step={5} />
              <p className="text-[10px] text-muted-foreground mt-1">Erzwingt Laden unter diesem SoC (0 = aus)</p>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-muted hover:bg-muted/80 transition-colors">Abbrechen</button>
            <button
              onClick={() => onSave(editing)}
              disabled={!draft.name || !draft.charger}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              <Save className="w-4 h-4" />
              {editing === "new" ? "Hinzufuegen" : "Speichern"}
            </button>
          </div>
        </div>
      )}

      {/* Loadpoint list */}
      {editing === null && (
        <div className="space-y-2">
          {config.loadpoints.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">Noch keine Ladepunkte konfiguriert.</p>
          ) : (
            config.loadpoints.map((lp, i) => {
              const modeInfo = CHARGING_MODES.find((m) => m.mode === lp.mode);
              return (
                <div key={i} className="flex items-center gap-4 p-4 rounded-xl bg-muted/30 border border-border/40 group card-hover">
                  <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                    <Zap className="w-5 h-5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{lp.name || "(Ohne Name)"}</p>
                    <p className="text-xs text-muted-foreground">
                      {lp.charger} | {modeInfo?.icon} {modeInfo?.label ?? lp.mode} | {lp.min_current}–{lp.max_current} A | {lp.phases}P{lp.target_soc && lp.target_soc < 100 ? ` | Ziel ${lp.target_soc}%` : ""}{lp.min_soc ? ` | Min ${lp.min_soc}%` : ""}
                    </p>
                  </div>
                  <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => onEdit(i)} className="p-2 hover:bg-muted rounded-lg transition-colors" title="Bearbeiten">
                      <Pencil className="w-4 h-4 text-muted-foreground" />
                    </button>
                    <button onClick={() => onDelete(i)} className="p-2 hover:bg-destructive/10 rounded-lg transition-colors" title="Loeschen">
                      <Trash2 className="w-4 h-4 text-destructive/70" />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// TAB: Fahrzeuge
// ===========================================================================

function TabFahrzeuge({ config, editing, draft, setDraft, onAdd, onEdit, onDelete, onSave, onCancel }: {
  config: WaldConfig;
  editing: number | "new" | null;
  draft: Record<string, any>;
  setDraft: (d: Record<string, any>) => void;
  onAdd: () => void;
  onEdit: (i: number) => void;
  onDelete: (i: number) => void;
  onSave: (i: number | "new") => void;
  onCancel: () => void;
}) {
  const inputClass = "w-full rounded-lg bg-muted/50 border border-border/60 px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/40 transition-colors";

  return (
    <div className="glass-panel rounded-2xl p-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-medium">Fahrzeuge</h3>
        {editing === null && (
          <button onClick={onAdd} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-primary hover:bg-primary/10 rounded-lg transition-colors">
            <Plus className="w-4 h-4" /> Hinzufuegen
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground mb-4">Fahrzeuge fuer Ladestand-Anzeige und intelligente Ladeplanung (optional).</p>

      {/* Editing form */}
      {editing !== null && (
        <div className="space-y-4 p-4 rounded-xl bg-muted/20 border border-border/40">
          <h4 className="text-sm font-medium">{editing === "new" ? "Neues Fahrzeug" : "Fahrzeug bearbeiten"}</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Name<span className="text-destructive ml-0.5">*</span></label>
              <input type="text" value={draft.name ?? ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={inputClass} placeholder="z.B. Model 3" />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Hersteller</label>
              <input type="text" value={draft.manufacturer ?? ""} onChange={(e) => setDraft({ ...draft, manufacturer: e.target.value })} className={inputClass} placeholder="z.B. Tesla, VW, BMW" />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Batteriekapazitaet (kWh)</label>
              <input type="number" value={draft.battery_kwh ?? 60} onChange={(e) => setDraft({ ...draft, battery_kwh: Number(e.target.value) })} className={inputClass} min={1} max={200} step={1} />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">VIN (optional)</label>
              <input type="text" value={draft.vin ?? ""} onChange={(e) => setDraft({ ...draft, vin: e.target.value })} className={inputClass} placeholder="Fahrzeugidentifikationsnummer" />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Ladepunkt (optional)</label>
              <select value={draft.loadpoint ?? ""} onChange={(e) => setDraft({ ...draft, loadpoint: e.target.value })} className={inputClass}>
                <option value="">Keiner</option>
                {config.loadpoints.map((lp) => (
                  <option key={lp.name} value={lp.name}>{lp.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-muted hover:bg-muted/80 transition-colors">Abbrechen</button>
            <button
              onClick={() => onSave(editing)}
              disabled={!draft.name}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              <Save className="w-4 h-4" />
              {editing === "new" ? "Hinzufuegen" : "Speichern"}
            </button>
          </div>
        </div>
      )}

      {/* Vehicle list */}
      {editing === null && (
        <div className="space-y-2">
          {config.vehicles.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">Noch keine Fahrzeuge konfiguriert.</p>
          ) : (
            config.vehicles.map((v, i) => (
              <div key={i} className="flex items-center gap-4 p-4 rounded-xl bg-muted/30 border border-border/40 group card-hover">
                <span className="text-2xl">🚗</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{v.name || "(Ohne Name)"}</p>
                  <p className="text-xs text-muted-foreground">
                    {v.manufacturer ? `${v.manufacturer} | ` : ""}{v.battery_kwh} kWh{v.loadpoint ? ` | ${v.loadpoint}` : ""}
                  </p>
                </div>
                <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button onClick={() => onEdit(i)} className="p-2 hover:bg-muted rounded-lg transition-colors" title="Bearbeiten">
                    <Pencil className="w-4 h-4 text-muted-foreground" />
                  </button>
                  <button onClick={() => onDelete(i)} className="p-2 hover:bg-destructive/10 rounded-lg transition-colors" title="Loeschen">
                    <Trash2 className="w-4 h-4 text-destructive/70" />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// TAB: System (preserved from original page)
// ===========================================================================

function TabSystem({
  updateInfo, updateChecking, updateRunning, updateMessage, logs,
  onCheckUpdate, onRunUpdate, onRefreshLogs, onSendCommand,
}: {
  updateInfo: UpdateInfo | null;
  updateChecking: boolean;
  updateRunning: boolean;
  updateMessage: string;
  logs: Array<{ id: number; level: string; source: string; message: string; created_at: string }>;
  onCheckUpdate: () => void;
  onRunUpdate: () => void;
  onRefreshLogs: () => void;
  onSendCommand: (action: string) => void;
}) {
  return (
    <div className="space-y-6">
      {/* Update Panel */}
      <div className="glass-panel rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-muted-foreground">Software-Update</h3>
          <button
            onClick={onCheckUpdate}
            disabled={updateChecking}
            className="p-1.5 hover:bg-muted rounded-lg transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${updateChecking ? "animate-spin" : ""}`} />
          </button>
        </div>

        {updateInfo && (
          <div className="space-y-3">
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
              <div className="text-xs text-muted-foreground">{updateInfo.current_date}</div>
            </div>

            {updateInfo.update_available ? (
              <div className="flex items-center justify-between p-3 rounded-xl bg-primary/10 border border-primary/20">
                <div className="flex items-center gap-2">
                  <Download className="w-4 h-4 text-primary" />
                  <span className="text-sm font-medium">
                    Update verfuegbar — {updateInfo.behind} {updateInfo.behind === 1 ? "Commit" : "Commits"} hinter origin/main
                  </span>
                </div>
                <button
                  onClick={onRunUpdate}
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

            {updateMessage && <p className="text-sm text-muted-foreground">{updateMessage}</p>}
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
          <button onClick={() => onSendCommand("restart_client")} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg text-sm transition-colors">
            Client neu starten
          </button>
          <button onClick={() => onSendCommand("reload_config")} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg text-sm transition-colors">
            Config neu laden
          </button>
          <button onClick={() => onSendCommand("cleanup_db")} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg text-sm transition-colors">
            Datenbank bereinigen
          </button>
        </div>
      </div>

      {/* Logs */}
      <div className="glass-panel rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-muted-foreground">Logs</h3>
          <button onClick={onRefreshLogs} className="p-1.5 hover:bg-muted rounded-lg transition-colors">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
        <div className="space-y-1 max-h-96 overflow-y-auto">
          {logs.length === 0 ? (
            <p className="text-sm text-muted-foreground">Keine Logs vorhanden.</p>
          ) : logs.map((log) => (
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
    </div>
  );
}

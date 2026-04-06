"use client";

import { useState, useCallback, useMemo } from "react";
import {
  Zap, ChevronRight, ChevronLeft, Check, Plus, Trash2, Loader2,
  Radio, Sun, Cpu, Wifi, Plug, Cloud, BatteryCharging, PlugZap,
  CircleDot, CircleOff, SunMedium, SunDim, Home,
} from "lucide-react";
import {
  METER_TEMPLATES,
  CHARGER_TEMPLATES,
  CHARGING_MODES,
  type MeterTemplate,
  type ChargerTemplate,
  type FieldDef,
} from "@/lib/device-templates";
import type { WaldConfig } from "@/lib/config";

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */

interface MeterEntry {
  id: string;
  templateType: string;
  name: string;
  values: Record<string, unknown>;
}

interface ChargerEntry {
  id: string;
  templateType: string;
  name: string;
  values: Record<string, unknown>;
}

interface LoadpointEntry {
  id: string;
  name: string;
  charger: string;
  mode: string;
  min_current: number;
  max_current: number;
  phases: number;
}

type Step = 0 | 1 | 2 | 3 | 4;

const STEP_LABELS = ["Willkommen", "Messgeraet", "Wallbox", "Ladepunkt", "Fertig"];

/* ------------------------------------------------------------------ */
/*  Utility: unique id                                                */
/* ------------------------------------------------------------------ */

let _idCounter = 0;
function uid(): string {
  return `_${Date.now()}_${++_idCounter}`;
}

/* ------------------------------------------------------------------ */
/*  Icon helper (maps emoji/strings to lucide components)             */
/* ------------------------------------------------------------------ */

function DeviceIcon({ icon, className }: { icon: string; className?: string }) {
  const c = className ?? "w-5 h-5";
  switch (icon) {
    case "⚡": return <Zap className={c} />;
    case "☀️": return <Sun className={c} />;
    case "🔆": return <SunMedium className={c} />;
    case "📊": return <Radio className={c} />;
    case "🔧": return <Cpu className={c} />;
    case "🔌": return <Plug className={c} />;
    case "🟢": return <CircleDot className={c} />;
    case "🏠": return <Home className={c} />;
    default:  return <Wifi className={c} />;
  }
}

function ModeIcon({ mode, className }: { mode: string; className?: string }) {
  const c = className ?? "w-5 h-5";
  switch (mode) {
    case "off":   return <CircleOff className={c} />;
    case "now":   return <Zap className={c} />;
    case "minpv": return <SunDim className={c} />;
    case "pv":    return <Sun className={c} />;
    default:      return <CircleDot className={c} />;
  }
}

/* ------------------------------------------------------------------ */
/*  Main Wizard Component                                             */
/* ------------------------------------------------------------------ */

export default function SetupWizard() {
  // --- Wizard navigation ---
  const [step, setStep] = useState<Step>(0);

  // --- Step 1: Site ---
  const [siteName, setSiteName] = useState("Mein Zuhause");
  const [gridLimit, setGridLimit] = useState(11);
  const [buffer, setBuffer] = useState(100);
  const [gridPrice, setGridPrice] = useState(0.27);
  const [feedinPrice, setFeedinPrice] = useState(0.065);

  // --- Step 2: Meters ---
  const [meters, setMeters] = useState<MeterEntry[]>([]);
  const [selectedMeterTemplate, setSelectedMeterTemplate] = useState<string | null>(null);
  const [meterFormValues, setMeterFormValues] = useState<Record<string, unknown>>({});

  // --- Step 3: Chargers ---
  const [chargers, setChargers] = useState<ChargerEntry[]>([]);
  const [selectedChargerTemplate, setSelectedChargerTemplate] = useState<string | null>(null);
  const [chargerFormValues, setChargerFormValues] = useState<Record<string, unknown>>({});

  // --- Step 4: Loadpoints ---
  const [loadpoints, setLoadpoints] = useState<LoadpointEntry[]>([]);
  const [lpName, setLpName] = useState("");
  const [lpCharger, setLpCharger] = useState("");
  const [lpMode, setLpMode] = useState("pv");
  const [lpMinCurrent, setLpMinCurrent] = useState(6);
  const [lpMaxCurrent, setLpMaxCurrent] = useState(16);
  const [lpPhases, setLpPhases] = useState(3);

  // --- Step 5: Save ---
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");

  // --- Validation ---
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Determine effective steps (skip loadpoint step if no chargers)
  const hasChargers = chargers.length > 0;

  const effectiveSteps = useMemo(() => {
    if (hasChargers) return [0, 1, 2, 3, 4] as Step[];
    return [0, 1, 2, 4] as Step[]; // skip step 3
  }, [hasChargers]);

  const currentStepIndex = effectiveSteps.indexOf(step);

  function goNext() {
    const nextIdx = currentStepIndex + 1;
    if (nextIdx < effectiveSteps.length) {
      setStep(effectiveSteps[nextIdx]);
    }
  }

  function goBack() {
    const prevIdx = currentStepIndex - 1;
    if (prevIdx >= 0) {
      setStep(effectiveSteps[prevIdx]);
    }
  }

  // --- Step 1 validation ---
  function validateStep1(): boolean {
    const e: Record<string, string> = {};
    if (!siteName.trim()) e.siteName = "Name erforderlich";
    if (gridLimit <= 0) e.gridLimit = "Muss groesser als 0 sein";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  // --- Step 2: add meter ---
  function selectMeterTemplate(type: string) {
    const tpl = METER_TEMPLATES.find(t => t.type === type);
    if (!tpl) return;
    setSelectedMeterTemplate(type);
    setMeterFormValues({ ...tpl.defaults });
    setErrors({});
  }

  function addMeter() {
    const tpl = METER_TEMPLATES.find(t => t.type === selectedMeterTemplate);
    if (!tpl) return;
    // validate required
    const e: Record<string, string> = {};
    for (const f of tpl.fields) {
      if (f.required && !meterFormValues[f.key]) {
        e[`meter_${f.key}`] = `${f.label} erforderlich`;
      }
    }
    if (Object.keys(e).length > 0) { setErrors(e); return; }

    const name = (meterFormValues.name as string) || tpl.label;
    setMeters(prev => [...prev, {
      id: uid(),
      templateType: tpl.type,
      name,
      values: { ...meterFormValues },
    }]);
    setSelectedMeterTemplate(null);
    setMeterFormValues({});
    setErrors({});
  }

  function removeMeter(id: string) {
    setMeters(prev => prev.filter(m => m.id !== id));
  }

  // --- Step 3: add charger ---
  function selectChargerTemplate(type: string) {
    const tpl = CHARGER_TEMPLATES.find(t => t.type === type);
    if (!tpl) return;
    setSelectedChargerTemplate(type);
    setChargerFormValues({ ...tpl.defaults });
    setErrors({});
  }

  function addCharger() {
    const tpl = CHARGER_TEMPLATES.find(t => t.type === selectedChargerTemplate);
    if (!tpl) return;
    const e: Record<string, string> = {};
    for (const f of tpl.fields) {
      if (f.required && !chargerFormValues[f.key]) {
        e[`charger_${f.key}`] = `${f.label} erforderlich`;
      }
    }
    if (Object.keys(e).length > 0) { setErrors(e); return; }

    const name = (chargerFormValues.name as string) || tpl.label;
    setChargers(prev => [...prev, {
      id: uid(),
      templateType: tpl.type,
      name,
      values: { ...chargerFormValues },
    }]);
    setSelectedChargerTemplate(null);
    setChargerFormValues({});
    setErrors({});
  }

  function removeCharger(id: string) {
    setChargers(prev => prev.filter(c => c.id !== id));
    // Also remove loadpoints referencing this charger
    const charger = chargers.find(c => c.id === id);
    if (charger) {
      setLoadpoints(prev => prev.filter(lp => lp.charger !== charger.name));
    }
  }

  // --- Step 4: add loadpoint ---
  function addLoadpoint() {
    const e: Record<string, string> = {};
    if (!lpName.trim()) e.lpName = "Name erforderlich";
    if (!lpCharger) e.lpCharger = "Wallbox auswaehlen";
    if (Object.keys(e).length > 0) { setErrors(e); return; }

    setLoadpoints(prev => [...prev, {
      id: uid(),
      name: lpName.trim(),
      charger: lpCharger,
      mode: lpMode,
      min_current: lpMinCurrent,
      max_current: lpMaxCurrent,
      phases: lpPhases,
    }]);
    setLpName("");
    setErrors({});
  }

  function removeLoadpoint(id: string) {
    setLoadpoints(prev => prev.filter(lp => lp.id !== id));
  }

  // --- Build config ---
  function buildConfig(): WaldConfig {
    return {
      site: {
        name: siteName.trim(),
        grid_limit_kw: gridLimit,
        buffer_w: buffer,
        priority_soc: 0,
        grid_price_eur_kwh: gridPrice,
        feedin_price_eur_kwh: feedinPrice,
      },
      meters: meters.map(m => {
        const tpl = METER_TEMPLATES.find(t => t.type === m.templateType);
        const vals = m.values;
        const registerMap = vals.register_map ? (() => {
          try { return JSON.parse(vals.register_map as string); } catch { return undefined; }
        })() : undefined;
        return {
          name: m.name,
          type: m.templateType,
          host: (vals.host as string) || "",
          port: Number(vals.port) || 502,
          unit_id: Number(vals.unit_id) || 0,
          ...(registerMap ? { register_map: registerMap } : {}),
        };
      }),
      chargers: chargers.map(c => ({
        name: c.name,
        type: c.templateType,
        host: (c.values.host as string) || "",
        port: Number(c.values.port) || 502,
        unit_id: Number(c.values.unit_id) || 0,
      })),
      loadpoints: loadpoints.map(lp => ({
        name: lp.name,
        charger: lp.charger,
        mode: lp.mode,
        min_current: lp.min_current,
        max_current: lp.max_current,
        phases: lp.phases,
      })),
      vehicles: [],
      database: {
        path: "wald-ems.db",
        retention_days: 30,
      },
    };
  }

  // --- Save ---
  async function handleSave() {
    setSaving(true);
    setSaveError("");
    try {
      const config = buildConfig();
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error("Speichern fehlgeschlagen");

      // Trigger config reload
      await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "reload_config" }),
      });

      setSaved(true);
    } catch (err: any) {
      setSaveError(err.message || "Unbekannter Fehler");
    } finally {
      setSaving(false);
    }
  }

  // --- Can proceed? ---
  function canProceed(): boolean {
    switch (step) {
      case 0: return !!siteName.trim() && gridLimit > 0;
      case 1: return meters.length > 0;
      case 2: return true; // chargers optional
      case 3: return true; // loadpoints optional if chargers exist
      default: return false;
    }
  }

  function handleNext() {
    if (step === 0 && !validateStep1()) return;
    if (step === 1 && meters.length === 0) {
      setErrors({ meters: "Mindestens ein Messgeraet erforderlich" });
      return;
    }
    goNext();
  }

  /* ================================================================ */
  /*  RENDER                                                          */
  /* ================================================================ */

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="glass-header sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
            <Zap className="w-5 h-5 text-primary" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight">Wald EMS</h1>
        </div>
        <span className="text-sm text-muted-foreground">Einrichtung</span>
      </header>

      <main className="flex-1 max-w-2xl w-full mx-auto px-4 py-8 space-y-8">
        {/* Step indicator */}
        <StepIndicator steps={effectiveSteps} current={step} labels={STEP_LABELS} />

        {/* Step content */}
        <div className="glass-panel rounded-2xl p-6 sm:p-8">
          {step === 0 && (
            <StepWelcome
              siteName={siteName} setSiteName={setSiteName}
              gridLimit={gridLimit} setGridLimit={setGridLimit}
              buffer={buffer} setBuffer={setBuffer}
              gridPrice={gridPrice} setGridPrice={setGridPrice}
              feedinPrice={feedinPrice} setFeedinPrice={setFeedinPrice}
              errors={errors}
            />
          )}
          {step === 1 && (
            <StepMeters
              meters={meters}
              selectedTemplate={selectedMeterTemplate}
              formValues={meterFormValues}
              setFormValues={setMeterFormValues}
              onSelectTemplate={selectMeterTemplate}
              onAdd={addMeter}
              onRemove={removeMeter}
              onCancelTemplate={() => { setSelectedMeterTemplate(null); setErrors({}); }}
              errors={errors}
            />
          )}
          {step === 2 && (
            <StepChargers
              chargers={chargers}
              selectedTemplate={selectedChargerTemplate}
              formValues={chargerFormValues}
              setFormValues={setChargerFormValues}
              onSelectTemplate={selectChargerTemplate}
              onAdd={addCharger}
              onRemove={removeCharger}
              onCancelTemplate={() => { setSelectedChargerTemplate(null); setErrors({}); }}
              errors={errors}
            />
          )}
          {step === 3 && (
            <StepLoadpoints
              loadpoints={loadpoints}
              chargers={chargers}
              lpName={lpName} setLpName={setLpName}
              lpCharger={lpCharger} setLpCharger={setLpCharger}
              lpMode={lpMode} setLpMode={setLpMode}
              lpMinCurrent={lpMinCurrent} setLpMinCurrent={setLpMinCurrent}
              lpMaxCurrent={lpMaxCurrent} setLpMaxCurrent={setLpMaxCurrent}
              lpPhases={lpPhases} setLpPhases={setLpPhases}
              onAdd={addLoadpoint}
              onRemove={removeLoadpoint}
              errors={errors}
            />
          )}
          {step === 4 && (
            <StepDone
              config={buildConfig()}
              saving={saving}
              saved={saved}
              saveError={saveError}
              onSave={handleSave}
            />
          )}
        </div>

        {/* Navigation buttons */}
        {step !== 4 && (
          <div className="flex items-center justify-between">
            <button
              onClick={goBack}
              disabled={currentStepIndex === 0}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium
                         text-muted-foreground hover:text-foreground hover:bg-muted/60
                         transition-colors disabled:opacity-0 disabled:pointer-events-none"
            >
              <ChevronLeft className="w-4 h-4" />
              Zurueck
            </button>
            <button
              onClick={handleNext}
              disabled={!canProceed()}
              className="flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-medium
                         bg-primary text-primary-foreground hover:bg-primary/90
                         transition-colors disabled:opacity-40 disabled:pointer-events-none"
            >
              Weiter
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </main>
    </div>
  );
}

/* ================================================================== */
/*  Step Indicator                                                    */
/* ================================================================== */

function StepIndicator({ steps, current, labels }: { steps: Step[]; current: Step; labels: string[] }) {
  const currentIdx = steps.indexOf(current);
  return (
    <div className="flex items-center justify-center gap-2">
      {steps.map((s, i) => {
        const isActive = s === current;
        const isDone = i < currentIdx;
        return (
          <div key={s} className="flex items-center gap-2">
            {i > 0 && (
              <div className={`w-8 h-px transition-colors ${isDone ? "bg-primary" : "bg-border"}`} />
            )}
            <div className="flex flex-col items-center gap-1">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-medium transition-all
                  ${isActive
                    ? "bg-primary text-primary-foreground ring-4 ring-primary/20"
                    : isDone
                      ? "bg-primary/20 text-primary"
                      : "bg-muted text-muted-foreground"
                  }`}
              >
                {isDone ? <Check className="w-4 h-4" /> : i + 1}
              </div>
              <span className={`text-[10px] font-medium hidden sm:block ${isActive ? "text-foreground" : "text-muted-foreground"}`}>
                {labels[s]}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ================================================================== */
/*  Step 1: Welcome / Site                                            */
/* ================================================================== */

interface StepWelcomeProps {
  siteName: string; setSiteName: (v: string) => void;
  gridLimit: number; setGridLimit: (v: number) => void;
  buffer: number; setBuffer: (v: number) => void;
  gridPrice: number; setGridPrice: (v: number) => void;
  feedinPrice: number; setFeedinPrice: (v: number) => void;
  errors: Record<string, string>;
}

function StepWelcome({ siteName, setSiteName, gridLimit, setGridLimit, buffer, setBuffer, gridPrice, setGridPrice, feedinPrice, setFeedinPrice, errors }: StepWelcomeProps) {
  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <div className="w-14 h-14 rounded-2xl bg-primary/20 flex items-center justify-center mx-auto mb-4">
          <Zap className="w-8 h-8 text-primary" />
        </div>
        <h2 className="text-2xl font-bold tracking-tight">Wald EMS einrichten</h2>
        <p className="text-sm text-muted-foreground max-w-md mx-auto">
          Willkommen! In wenigen Schritten konfigurierst du dein lokales Energiemanagementsystem.
        </p>
      </div>

      <div className="space-y-4 max-w-md mx-auto">
        <FormField label="Name der Anlage" error={errors.siteName}>
          <input
            type="text"
            value={siteName}
            onChange={e => setSiteName(e.target.value)}
            placeholder="Mein Zuhause"
            className="form-input"
          />
        </FormField>

        <FormField label="Netzanschlussleistung (kW)" error={errors.gridLimit}>
          <input
            type="number"
            value={gridLimit}
            onChange={e => setGridLimit(Number(e.target.value))}
            min={1}
            step={0.5}
            className="form-input"
          />
        </FormField>

        <FormField label="Puffer (W)" hint="Sicherheitspuffer fuer Regelung">
          <input
            type="number"
            value={buffer}
            onChange={e => setBuffer(Number(e.target.value))}
            min={0}
            step={50}
            className="form-input"
          />
        </FormField>

        <div className="grid grid-cols-2 gap-4">
          <FormField label="Strompreis (EUR/kWh)">
            <input
              type="number"
              value={gridPrice}
              onChange={e => setGridPrice(Number(e.target.value))}
              min={0}
              step={0.01}
              className="form-input"
            />
          </FormField>
          <FormField label="Einspeiseverguetung (EUR/kWh)">
            <input
              type="number"
              value={feedinPrice}
              onChange={e => setFeedinPrice(Number(e.target.value))}
              min={0}
              step={0.001}
              className="form-input"
            />
          </FormField>
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Step 2: Meters                                                    */
/* ================================================================== */

interface StepMetersProps {
  meters: MeterEntry[];
  selectedTemplate: string | null;
  formValues: Record<string, unknown>;
  setFormValues: (v: Record<string, unknown>) => void;
  onSelectTemplate: (type: string) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
  onCancelTemplate: () => void;
  errors: Record<string, string>;
}

function StepMeters({ meters, selectedTemplate, formValues, setFormValues, onSelectTemplate, onAdd, onRemove, onCancelTemplate, errors }: StepMetersProps) {
  const tpl = selectedTemplate ? METER_TEMPLATES.find(t => t.type === selectedTemplate) : null;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-xl font-bold tracking-tight">Messgeraete</h2>
        <p className="text-sm text-muted-foreground">
          Fuege mindestens ein Messgeraet hinzu (z.B. Netzzaehler, PV-Wechselrichter).
        </p>
      </div>

      {/* Added meters */}
      {meters.length > 0 && (
        <div className="space-y-2">
          {meters.map(m => {
            const t = METER_TEMPLATES.find(x => x.type === m.templateType);
            return (
              <div key={m.id} className="flex items-center justify-between bg-muted/40 rounded-xl px-4 py-3">
                <div className="flex items-center gap-3">
                  {t && <DeviceIcon icon={t.icon} className="w-4 h-4 text-primary" />}
                  <div>
                    <p className="text-sm font-medium">{m.name}</p>
                    <p className="text-xs text-muted-foreground">{t?.label} &mdash; {(m.values.host as string) || ""}</p>
                  </div>
                </div>
                <button onClick={() => onRemove(m.id)} className="p-1.5 hover:bg-muted rounded-lg transition-colors text-muted-foreground hover:text-destructive">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Template selector or form */}
      {!selectedTemplate ? (
        <div>
          <p className="text-xs text-muted-foreground mb-3">Geraetetyp waehlen:</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {METER_TEMPLATES.map(t => (
              <TemplateCard
                key={t.type}
                icon={t.icon}
                label={t.label}
                description={t.description}
                onClick={() => onSelectTemplate(t.type)}
              />
            ))}
          </div>
        </div>
      ) : tpl ? (
        <DeviceForm
          template={tpl}
          values={formValues}
          onChange={setFormValues}
          onSubmit={onAdd}
          onCancel={onCancelTemplate}
          errors={errors}
          prefix="meter_"
          submitLabel="Geraet hinzufuegen"
        />
      ) : null}

      {errors.meters && (
        <p className="text-xs text-destructive">{errors.meters}</p>
      )}
    </div>
  );
}

/* ================================================================== */
/*  Step 3: Chargers                                                  */
/* ================================================================== */

interface StepChargersProps {
  chargers: ChargerEntry[];
  selectedTemplate: string | null;
  formValues: Record<string, unknown>;
  setFormValues: (v: Record<string, unknown>) => void;
  onSelectTemplate: (type: string) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
  onCancelTemplate: () => void;
  errors: Record<string, string>;
}

function StepChargers({ chargers, selectedTemplate, formValues, setFormValues, onSelectTemplate, onAdd, onRemove, onCancelTemplate, errors }: StepChargersProps) {
  const tpl = selectedTemplate ? CHARGER_TEMPLATES.find(t => t.type === selectedTemplate) : null;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-xl font-bold tracking-tight">Wallboxen</h2>
        <p className="text-sm text-muted-foreground">
          Optional: Fuege eine oder mehrere Wallboxen hinzu. Du kannst diesen Schritt ueberspringen.
        </p>
      </div>

      {chargers.length > 0 && (
        <div className="space-y-2">
          {chargers.map(c => {
            const t = CHARGER_TEMPLATES.find(x => x.type === c.templateType);
            return (
              <div key={c.id} className="flex items-center justify-between bg-muted/40 rounded-xl px-4 py-3">
                <div className="flex items-center gap-3">
                  {t && <DeviceIcon icon={t.icon} className="w-4 h-4 text-primary" />}
                  <div>
                    <p className="text-sm font-medium">{c.name}</p>
                    <p className="text-xs text-muted-foreground">{t?.label} &mdash; {(c.values.host as string) || ""}</p>
                  </div>
                </div>
                <button onClick={() => onRemove(c.id)} className="p-1.5 hover:bg-muted rounded-lg transition-colors text-muted-foreground hover:text-destructive">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {!selectedTemplate ? (
        <div>
          <p className="text-xs text-muted-foreground mb-3">Wallbox-Typ waehlen:</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {CHARGER_TEMPLATES.map(t => (
              <TemplateCard
                key={t.type}
                icon={t.icon}
                label={t.label}
                description={t.description}
                onClick={() => onSelectTemplate(t.type)}
              />
            ))}
          </div>
        </div>
      ) : tpl ? (
        <DeviceForm
          template={tpl}
          values={formValues}
          onChange={setFormValues}
          onSubmit={onAdd}
          onCancel={onCancelTemplate}
          errors={errors}
          prefix="charger_"
          submitLabel="Wallbox hinzufuegen"
        />
      ) : null}
    </div>
  );
}

/* ================================================================== */
/*  Step 4: Loadpoints                                                */
/* ================================================================== */

interface StepLoadpointsProps {
  loadpoints: LoadpointEntry[];
  chargers: ChargerEntry[];
  lpName: string; setLpName: (v: string) => void;
  lpCharger: string; setLpCharger: (v: string) => void;
  lpMode: string; setLpMode: (v: string) => void;
  lpMinCurrent: number; setLpMinCurrent: (v: number) => void;
  lpMaxCurrent: number; setLpMaxCurrent: (v: number) => void;
  lpPhases: number; setLpPhases: (v: number) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
  errors: Record<string, string>;
}

function StepLoadpoints(props: StepLoadpointsProps) {
  const { loadpoints, chargers, lpName, setLpName, lpCharger, setLpCharger, lpMode, setLpMode, lpMinCurrent, setLpMinCurrent, lpMaxCurrent, setLpMaxCurrent, lpPhases, setLpPhases, onAdd, onRemove, errors } = props;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-xl font-bold tracking-tight">Ladepunkte</h2>
        <p className="text-sm text-muted-foreground">
          Konfiguriere Ladepunkte fuer deine Wallboxen.
        </p>
      </div>

      {/* Added loadpoints */}
      {loadpoints.length > 0 && (
        <div className="space-y-2">
          {loadpoints.map(lp => (
            <div key={lp.id} className="flex items-center justify-between bg-muted/40 rounded-xl px-4 py-3">
              <div className="flex items-center gap-3">
                <PlugZap className="w-4 h-4 text-primary" />
                <div>
                  <p className="text-sm font-medium">{lp.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {lp.charger} &mdash; {CHARGING_MODES.find(m => m.mode === lp.mode)?.label || lp.mode} &mdash; {lp.min_current}-{lp.max_current}A / {lp.phases}P
                  </p>
                </div>
              </div>
              <button onClick={() => onRemove(lp.id)} className="p-1.5 hover:bg-muted rounded-lg transition-colors text-muted-foreground hover:text-destructive">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add loadpoint form */}
      <div className="space-y-4 p-4 rounded-xl bg-muted/20 border border-border/40">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Neuer Ladepunkt</p>

        <FormField label="Name" error={errors.lpName}>
          <input
            type="text"
            value={lpName}
            onChange={e => setLpName(e.target.value)}
            placeholder="Garage"
            className="form-input"
          />
        </FormField>

        <FormField label="Wallbox" error={errors.lpCharger}>
          <select
            value={lpCharger}
            onChange={e => setLpCharger(e.target.value)}
            className="form-input"
          >
            <option value="">Wallbox waehlen...</option>
            {chargers.map(c => (
              <option key={c.id} value={c.name}>{c.name}</option>
            ))}
          </select>
        </FormField>

        {/* Mode selector as cards */}
        <div>
          <label className="text-xs font-medium text-muted-foreground block mb-2">Lademodus</label>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {CHARGING_MODES.map(m => (
              <button
                key={m.mode}
                type="button"
                onClick={() => setLpMode(m.mode)}
                className={`flex flex-col items-center gap-1.5 p-3 rounded-xl text-xs font-medium border transition-all
                  ${lpMode === m.mode
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border/40 bg-muted/20 text-muted-foreground hover:border-border hover:bg-muted/40"
                  }`}
              >
                <ModeIcon mode={m.mode} className="w-5 h-5" />
                <span>{m.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <FormField label="Min Strom (A)">
            <input
              type="number"
              value={lpMinCurrent}
              onChange={e => setLpMinCurrent(Number(e.target.value))}
              min={6}
              max={32}
              className="form-input"
            />
          </FormField>
          <FormField label="Max Strom (A)">
            <input
              type="number"
              value={lpMaxCurrent}
              onChange={e => setLpMaxCurrent(Number(e.target.value))}
              min={6}
              max={32}
              className="form-input"
            />
          </FormField>
          <FormField label="Phasen">
            <div className="flex gap-2">
              {[1, 3].map(p => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setLpPhases(p)}
                  className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-all
                    ${lpPhases === p
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border/40 bg-muted/20 text-muted-foreground hover:border-border"
                    }`}
                >
                  {p}P
                </button>
              ))}
            </div>
          </FormField>
        </div>

        <button
          onClick={onAdd}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium
                     bg-primary/10 text-primary hover:bg-primary/20 transition-colors border border-primary/20"
        >
          <Plus className="w-4 h-4" />
          Ladepunkt hinzufuegen
        </button>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Step 5: Done / Summary                                            */
/* ================================================================== */

interface StepDoneProps {
  config: WaldConfig;
  saving: boolean;
  saved: boolean;
  saveError: string;
  onSave: () => void;
}

function StepDone({ config, saving, saved, saveError, onSave }: StepDoneProps) {
  if (saved) {
    return (
      <div className="text-center space-y-6 py-4">
        <div className="w-16 h-16 rounded-full bg-primary/20 flex items-center justify-center mx-auto">
          <Check className="w-8 h-8 text-primary" />
        </div>
        <div className="space-y-2">
          <h2 className="text-2xl font-bold tracking-tight">Einrichtung abgeschlossen</h2>
          <p className="text-sm text-muted-foreground">
            Die Konfiguration wurde gespeichert. Der Client wird die neue Konfiguration automatisch laden.
          </p>
        </div>
        <a
          href="/"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl text-sm font-medium
                     bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          Zum Dashboard
          <ChevronRight className="w-4 h-4" />
        </a>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-xl font-bold tracking-tight">Zusammenfassung</h2>
        <p className="text-sm text-muted-foreground">
          Pruefe die Konfiguration und speichere sie.
        </p>
      </div>

      {/* Site summary */}
      <SummarySection title="Anlage">
        <SummaryRow label="Name" value={config.site.name} />
        <SummaryRow label="Netzanschluss" value={`${config.site.grid_limit_kw} kW`} />
        <SummaryRow label="Puffer" value={`${config.site.buffer_w} W`} />
        <SummaryRow label="Strompreis" value={`${config.site.grid_price_eur_kwh} EUR/kWh`} />
        <SummaryRow label="Einspeiseverguetung" value={`${config.site.feedin_price_eur_kwh} EUR/kWh`} />
      </SummarySection>

      {/* Meters summary */}
      <SummarySection title={`Messgeraete (${config.meters.length})`}>
        {config.meters.map((m, i) => (
          <SummaryRow key={i} label={m.name} value={`${m.type} @ ${m.host}:${m.port}`} />
        ))}
      </SummarySection>

      {/* Chargers summary */}
      {config.chargers.length > 0 && (
        <SummarySection title={`Wallboxen (${config.chargers.length})`}>
          {config.chargers.map((c, i) => (
            <SummaryRow key={i} label={c.name} value={`${c.type} @ ${c.host}`} />
          ))}
        </SummarySection>
      )}

      {/* Loadpoints summary */}
      {config.loadpoints.length > 0 && (
        <SummarySection title={`Ladepunkte (${config.loadpoints.length})`}>
          {config.loadpoints.map((lp, i) => (
            <SummaryRow key={i} label={lp.name} value={`${lp.charger} / ${lp.mode} / ${lp.min_current}-${lp.max_current}A`} />
          ))}
        </SummarySection>
      )}

      {saveError && (
        <div className="p-3 rounded-xl bg-destructive/10 border border-destructive/20 text-sm text-destructive">
          {saveError}
        </div>
      )}

      <div className="flex justify-center pt-2">
        <button
          onClick={onSave}
          disabled={saving}
          className="flex items-center gap-2 px-8 py-3 rounded-xl text-sm font-semibold
                     bg-primary text-primary-foreground hover:bg-primary/90
                     transition-colors disabled:opacity-50"
        >
          {saving ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Speichere...
            </>
          ) : (
            <>
              <Check className="w-4 h-4" />
              Konfiguration speichern
            </>
          )}
        </button>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Shared UI Components                                              */
/* ================================================================== */

function TemplateCard({ icon, label, description, onClick }: { icon: string; label: string; description: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-start gap-3 p-4 rounded-xl border border-border/40 bg-muted/20
                 hover:border-primary/40 hover:bg-primary/5 transition-all text-left card-hover"
    >
      <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
        <DeviceIcon icon={icon} className="w-5 h-5 text-primary" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{description}</p>
      </div>
    </button>
  );
}

interface DeviceFormProps {
  template: MeterTemplate | ChargerTemplate;
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  onSubmit: () => void;
  onCancel: () => void;
  errors: Record<string, string>;
  prefix: string;
  submitLabel: string;
}

function DeviceForm({ template, values, onChange, onSubmit, onCancel, errors, prefix, submitLabel }: DeviceFormProps) {
  function updateField(key: string, value: unknown) {
    onChange({ ...values, [key]: value });
  }

  return (
    <div className="space-y-4 p-4 rounded-xl bg-muted/20 border border-border/40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <DeviceIcon icon={template.icon} className="w-4 h-4 text-primary" />
          <p className="text-sm font-medium">{template.label}</p>
        </div>
        <button onClick={onCancel} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
          Abbrechen
        </button>
      </div>

      {template.fields.map(f => (
        <FormField key={f.key} label={f.label} hint={f.help} error={errors[`${prefix}${f.key}`]}>
          {f.type === "select" ? (
            <select
              value={String(values[f.key] ?? "")}
              onChange={e => updateField(f.key, e.target.value)}
              className="form-input"
            >
              <option value="">Bitte waehlen...</option>
              {f.options?.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ) : (
            <input
              type={f.type}
              value={String(values[f.key] ?? "")}
              onChange={e => updateField(f.key, f.type === "number" ? Number(e.target.value) : e.target.value)}
              placeholder={f.placeholder}
              min={f.min}
              max={f.max}
              step={f.step}
              className="form-input"
            />
          )}
        </FormField>
      ))}

      <button
        onClick={onSubmit}
        className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium
                   bg-primary/10 text-primary hover:bg-primary/20 transition-colors border border-primary/20"
      >
        <Plus className="w-4 h-4" />
        {submitLabel}
      </button>
    </div>
  );
}

function FormField({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      {children}
      {hint && !error && <p className="text-[10px] text-muted-foreground">{hint}</p>}
      {error && <p className="text-[10px] text-destructive">{error}</p>}
    </div>
  );
}

function SummarySection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{title}</h3>
      <div className="space-y-1 bg-muted/20 rounded-xl p-3">
        {children}
      </div>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm py-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium mono text-xs">{value}</span>
    </div>
  );
}

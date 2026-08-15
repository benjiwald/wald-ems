# Wald EMS — Architecture & Site Notes

## Overview
Wald EMS is a self-hosted energy management system for Raspberry Pi, derived from the cloud-based Wania EMS. It combines the Next.js dashboard and Python Modbus client into a single local installation communicating via SQLite.

## Key Differences from Wania EMS
- **No Supabase** — SQLite replaces PostgreSQL
- **No MQTT** — Python writes directly to SQLite, Next.js reads from it
- **No multi-tenancy** — Single-site, no auth
- **YAML config** — `wald-ems.yaml` replaces DB-stored configuration
- **Two systemd services** — `wald-ems` (dashboard) + `wald-ems-client` (Modbus/control)

## Architecture

### Communication: SQLite (WAL mode)
- Python client writes `site_state` to `state` table every 10s
- Python client writes metrics to `telemetry` table every 30s
- Next.js reads via API routes, serves to browser
- Commands: Next.js inserts into `commands` table, Python polls every 1s
- SSE endpoint `/api/events` for real-time browser updates

### Dashboard (Next.js 15)
- `output: "standalone"` for Pi deployment
- `better-sqlite3` for database access (native module)
- Server-Sent Events for real-time updates
- No authentication (local network)
- Pages: `/` (dashboard), `/sessions`, `/settings`

### Python Client
- `db_handler.py` replaces `mqtt_handler.py`
- `config.py` reads YAML instead of Supabase RPC
- All drivers (Modbus, HTTP, vehicle APIs) unchanged from Wania
- 10s control loop, 30s telemetry interval

### Config
- Single YAML file: `wald-ems.yaml`
- Config path: `$WALD_EMS_CONFIG` → `./wald-ems.yaml` → `/opt/ems/wald-ems.yaml`
- Both Next.js and Python read the same file
- Python watches file mtime for hot-reload

## Build & Deploy
```bash
npm run build          # Next.js standalone build
npm run start          # Run standalone server
```

## Commands
```bash
npm run dev            # Development
npm run build          # Production build
npm run start          # Production server
```

---

# Site Setup — Puchheim Gasse 4, 3860 Heidenreichstein

## Network Map (LAN 10.10.10.0/24, Gateway UniFi 10.10.10.1)
| Host | IP | Funktion |
|---|---|---|
| Wald EMS Pi | `10.10.10.22` | Next.js Dashboard auf :7777, SQLite, Modbus-Client |
| Cerbo GX (Venus OS auf Raspberry Pi 4) | `10.10.10.70` | Victron-Steuerung, GUI v1 + GUI v2, SSH (root + Passwort) |
| mTec Wärmepumpe (KEBA KeEnergy h1000) | `10.10.10.73` | Web-HMI auf :80, Modbus TCP auf :502 (1-basiert) |
| Carlo Gavazzi Grid Meter (KX0610183001B) | RS485 → Cerbo | 3-phasiger Hauptzähler |

## Hardware-Inventar

### Photovoltaik (~19 kWp gesamt)
- **SMA 5 kW** — Hauptdach, AC-gekoppelt am AC-OUT vom Multi
- **2× Hoymiles HM-1200 / HM-400** — Dach-Mikroinverter, AC-gekoppelt
- **Hoymiles HMS-2000-4T** — "Schupfn" (Schuppen), AC-gekoppelt
- **PV Balkon (Shelly 3EM Messung)** — Balkonkraftwerk
- **2× Victron SmartSolar MPPT 250/70** — DC-gekoppelt am Cerbo

### Speicher (gesamt ~36 kWh nutzbar)
- **4× Pytes V5** (4×100 Ah / 5,12 kWh = 20,48 kWh nominal)
  - CAN-Bus BMS LV @ 500 kbit/s an Cerbo `can0`
  - Master/Slaves müssen ALLE gemeinsam ein-/ausgeschaltet werden!
  - Reboot-Reihenfolge: ALLE aus → 30 s warten → Slaves zuerst (4,3,2) → Master (1) zuletzt
  - **Bei Einzel-Reboot eines Packs erkennt der Master nur sich selbst** → System verliert 75% Kapazität (Diagnose: `NrOfModulesOnline = 1`)
  - Pytes-Default-CVL: 53,0 V (sehr konservativ, ~95% LFP-Voll)
- **16× EVE LF280K v3** (real ~309 Ah ≈ 304 Ah BMS-Reading = 15,82 kWh nominal)
  - 16S1P-Pack, JK-BMS via USB → `/dev/ttyUSB1` → dbus-serialbattery
  - JK-BMS-CVL: 56,8 V, MIN_CELL 3,00 V, FLOAT 3,45 V, MAX 3,55 V

### MultiPlus-II 48/3000/35-32 (3-Phasen-Setup)
- 3 Einheiten am VE.Bus (Master + 2 Slaves)
- Gesamt: **9 kVA / ~7,2 kW kontinuierlich AC-Output**
- 32 A AC-In Limit pro Phase, 35 A Charger pro Unit
- Firmware v552, MK3 Interface, VRM-Instanz 288
- Position der AC-Lasten: **Nur AC-Ausgang** (alle Verbraucher hinter Multi)
- ESS-Modus: "Optimiert ohne BatteryLife"

### Wärmepumpe mTec (KEBA KeEnergy h1000)
- 10 kW Heizleistung, COP 5-6 bei mildem Wetter
- **KEIN Pufferspeicher** — Estrich (FBH) dient als thermischer Puffer (~4,4 kWh/K, ~8-10 kWh nutzbar bei ±2 K Komfort)
- Service-Passwort (Techniker, einfach): `100`
- KEIN SG-Ready-Modul angeschlossen — Steuerung erfolgt direkt via Modbus TCP

### Ladestationen
- **NRG Kick** Wallbox in der Garage ("Einfahrt")
  - Modbus TCP, 1-3 phasig
  - **WICHTIG: Renault Zoe immer 3-phasig laden, NIE phasenumschalten** (Zoe ist bei 1P sehr ineffizient + verliert oft die Session)

### Fahrzeuge
- **Renault Zoe** — 40 kWh Akku, Lademodus PV (Mode `pv`), Target 90%

## Cerbo / Victron-Konfiguration

### DBus Battery Services
| Service | Pack | Reported InstalledCapacity |
|---|---|---|
| `com.victronenergy.battery.socketcan_can0` | Pytes (CAN) | 100 Ah pro Pack × 4 (wenn Stack OK) → 400 Ah |
| `com.victronenergy.battery.ttyUSB1` | EVE/JK-BMS (USB) | 304 Ah |
| `com.victronenergy.battery.aggregator` | **Battery Aggregator** | **704 Ah** (Summe) |

### BatteryAggregator (Community-Tool)
- Quelle: `pulquero/BatteryAggregator` (GitHub)
- Pfad: `/data/BatteryAggregator/battery_service.py`
- Service: `/service/BatteryAggregator/`
- Konfig-Pfad (default): `/data/setupOptions/BatteryAggregator/config.json`
  - Aktuell: nur `optionsSet` (leer) → läuft mit Defaults
- **Algorithmus:** `min(C-Rate) × Total Capacity`
  - CCL aggregiert = min(CCL_Pack/Capacity_Pack) × InstalledCapacity_total
  - DCL aggregiert = min(DCL_Pack/Capacity_Pack) × InstalledCapacity_total
  - → Der Pack mit der niedrigsten C-Rate limitiert das Gesamtsystem proportional

### dbus-serialbattery
- Pfad: `/data/etc/dbus-serialbattery/`
- Config: `config.ini`:
  ```
  MAX_BATTERY_CHARGE_CURRENT = 150.0
  MAX_BATTERY_DISCHARGE_CURRENT = 150.0
  MIN_CELL_VOLTAGE = 3.00
  MAX_CELL_VOLTAGE = 3.55
  FLOAT_CELL_VOLTAGE = 3.45
  ```

### DVCC (Ladekontrolle)
- DVCC: aktiv ✓
- Maximaler Ladestrom: 330 A (effektiver Cap, da BMS nur ~330 A meldet)
- Maximale Ladespannung: **56,0 V** (überstimmt Pytes 53,0 und JK 56,8)
- Steuerndes BMS: Battery Aggregator
- SVS/STS/SCS: alle AUS

### ESS-Settings
- Modus: **Optimiert ohne BatteryLife**
- Netz-Messung: Externer Zähler (Carlo Gavazzi)
- Mehrphasige Regulierung: **Summe aller Phasen** (für 3-phasiges Setup)
- SoC Mindestwert Entladung: **5%** ⚠️ (Empfehlung: 10-15% für Lebensdauer)
- Sustain Voltage: 49,0 V (3,06 V/Zelle — sehr tief)
- Wechselrichter-Leistung begrenzen: AUS
- Sollwert Netz: 0 W
- Maximale Einspeisung: 14.500 W
- AC-PV + DC-PV Überschusseinspeisung: EIN
- Lastspitzenkappung: "Nur oberhalb Mindest-SoC"

### VEConfigure-Settings (offline-Tool)
- Charger Tab:
  - Lithium batteries: ✓
  - Configured for VE.Bus BMS: AUS (kein VE.Bus BMS vorhanden)
  - Charge curve: Fixed
  - Absorption: 55,00 V (3,44 V/Zelle) — wird von DVCC 56,0 V überstimmt
  - Float: 54,00 V (3,375 V/Zelle)
  - Charge current: 35 A pro Unit
- ESS-Assistant geladen, Battery capacity: 400 Ah deklariert (sollte 704 Ah sein)
- Cut-off Voltages (LFP-konform):
  - 0,005 C: 46,50 V (2,91 V/Zelle)
  - 0,25 C: 45,50 V (2,84 V/Zelle)
- Advanced "Limit internal charger to prioritize other energy sources": **AUS** (Sustain Voltage 52,00 V)

### KEBA Wärmepumpe — Modbus TCP
- **Adressierung: 1-basiert** (Reg 1502 in Doku = `read_holding_registers(1502, 1, unit=1)`)
- Pymodbus auf Cerbo: 2.3.0 (sync API: `from pymodbus.client.sync import ModbusTcpClient`)

**Wichtigste Lese-Register:**
| Reg | Name | Skalierung |
|---|---|---|
| 700 | Operating Hours | × 1 h |
| 701 | Total Heating Energy (lifetime) | × 1 kWh |
| 702 | Total Electrical Energy (lifetime) | × 1 kWh |
| 703 | HP State (0=Standby, 2=Auto, 3=Defrost) | enum |
| 705 | Flow Temperature | × 0,1 °C |
| 706 | Heat Power Consumption | × 1 W |
| 707 | Electrical Power Consumption | × 1 W |
| 708 | Source In Temperature | × 0,1 °C |
| 709 | Source Out Temperature | × 0,1 °C |
| 710 | Reflux Temperature | × 0,1 °C |
| 718 | Compressor Modulation | × 1 % |
| 401 | DHW Top Temperature | × 0,1 °C |
| 1500 | System Operating Mode | enum |
| 1502 | Exterior Temperature | × 0,1 °C |
| 1 | HK1 Actual Room Temperature | × 0,1 °C |
| 4 | HK1 Room Set Temperature | × 0,1 °C |
| 15/16 | HK1 Vorlauf/Rücklauf | × 0,1 °C |

**Wichtigste Schreib-Register (für PV/EG-Steuerung):**
| Reg | Name | Werte |
|---|---|---|
| 7 | HK1 Operating Mode | 0=Standby, 1=Timer, 2=Day, 3=Night, 4=Vacation, 5=Party |
| **12** | **HK1 Heat Request Set External** | **0=Off, 1=On** ← SG-Ready-Äquivalent |
| 403 | DHW Operating Mode | 0=Off, 1=Auto, 2=On, **3=Heat Up** ← Boost |
| 713 | WP Operating Mode | 0=Off, 1=On, 2=Backup |
| 1500 | System Mode | 0=Standby, 1=Hot Water, 2=Auto Heat, 4=Full Auto |

**Modbus-Test (auf Cerbo):**
```python
from pymodbus.client.sync import ModbusTcpClient
c = ModbusTcpClient('10.10.10.73', port=502)
c.connect()
r = c.read_holding_registers(1502, 1, unit=1)  # Außentemp
print(f'{r.registers[0]/10} °C')
```

## Stromtarif & Energiegemeinschaft

### Hauptlieferant: WEB Grünstrom (resident)
- Energiepreis: 12,90 ct/kWh netto = 15,48 ct brutto (ab 04/2025)
- Grundpreis: 3,50 €/Monat
- 100% Erneuerbare Stromkennzeichnung (77% Wind, 19% Sonne, 4% sonstige)

### Netznutzung: Netz Niederösterreich
- Tarif: **NE 7, nicht gemessene Leistung, 4,00 kW** ⚠️ (real bis 17,9 kW Peak — ungewöhnlich hoch für 4 kW Anschluss)
- Arbeitspreis HT: 8,79 ct/kWh (2026)
- Netzverlust: 0,38 ct/kWh
- Messleistungen: 0,072 €/Tag

### Steuern & Abgaben
- Elektrizitätsabgabe: 0,1 ct/kWh (2026, war 1,5 ct in 2025 — Strompreisbremse!)
- EAG Pauschale: 0,052 €/Tag
- EAG Förderbeitrag (NV): 5,83 ct/kWh (2026)

### All-in-Preis Bezug (WEB)
- Variabel: ~26 ct/kWh brutto
- Fix: ~165 €/Jahr Grundgebühren

### EG "G5 Nord" (Erneuerbare Energie Gemeinschaft, Reingers)
- ZVR: 1563018472
- Mitgliedsnummer: RC107217-000155
- Beitritt: 16.04.2026
- Abrechnung: Quartalsweise via So-Strom GmbH
- **Bezugstarif: 9,00 ct netto / 10,80 ct brutto**
- **Einspeisetarif: 7,00 ct netto** (Mitglied 0% USt — Nichtunternehmer)

### EG-Vorteil bei regionaler EG (G5 = regional, -57% Netzgebühren)
| | WEB Standard | EG G5 |
|---|---|---|
| Energie brutto | 15,48 ct | 10,80 ct |
| Netznutzung | 10,55 ct | ~4,53 ct |
| EAG-Förderbeitrag | 7,00 ct | **0** (entfällt EG-intern) |
| **Total brutto** | **~26 ct** | **~16 ct** |

→ **Ersparnis ~10 ct/kWh bei EG-Bezug**

## Verbrauchsprofil (EVN/Netzbetreiber-Daten)

### Jahresbilanz 2025
- Gesamt-Bezug: 5.093 kWh = 1.375 € Bezugskosten brutto
- Gesamt-Einspeisung: 6.145 kWh = 399 € Erlös bei 6,5 ct
- Netto-Stromsaldo: **−976 €/Jahr**
- Tatsächliche Rechnung 03/2025 - 02/2026: **1.700 € brutto**

### Saisonalität (extreme Spreizung)
| | Bezug | Einspeisung | PV-Deckung |
|---|---|---|---|
| **Mai-Aug** | <100 kWh/Monat | >800 kWh/Monat | 96-100% |
| **Sep-Okt** | 100-415 kWh | 28-478 kWh | 6-82% |
| **Nov 2025** | **874 kWh** | 7 kWh | **1%** |
| **Dez 2025** | **1.263 kWh** | 4 kWh | **0%** |
| **Jan 2026** | **1.175 kWh** | 22 kWh | **2%** |

### Tagesprofil-Insights
- **Bezug-Peak: 06-09 Uhr** (1.167 kWh/Jahr = 23%)
- **Bezug-Tief: 12 Uhr** (98 kWh/Jahr — PV deckt)
- Bezug-zweiter-Peak: 18-22 Uhr (1.167 kWh/Jahr = 23%)
- Einspeise-Peak: 14 Uhr (997 kWh/Jahr)
- **Höchste Bezugsspitze: 17,9 kW** (29.11.2025 09:15) — Wärmepumpe + Wallbox + Geräte zusammen
- Top-10-Bezugsspitzen alle im Spätherbst/Winter zwischen 17:00-22:00

### VRM-Daten (Total Consumption inkl. PV-Eigenverbrauch)
| Monat | Total Cons | EVN Bezug | Aus PV+Speicher |
|---|---|---|---|
| Nov 25 | ~1.370 | 874 | ~496 |
| Dez 25 | ~1.500 | 1.263 | ~237 |
| Jan 26 | ~1.670 | 1.175 | ~495 |

→ Wärmepumpe ist Hauptverbraucher (~70% des Winter-Verbrauchs ≈ 3.000 kWh/Saison)
→ PV-Solar im Winter sehr klein: Dez 25 nur ~300 kWh, Jan 26 nur ~250 kWh
→ **Im Winter kann PV die WP NICHT versorgen** — Optimierung muss über günstigen EG-Bezug + Lastverlagerung laufen

### Q1 2026 historisch schlechter als Q1 2025
- Bezug: +6%, Einspeisung: −12%
- Vermutete Ursache: **Pytes-Stack hatte nur 1 von 4 Modulen aktiv** (siehe oben Reboot-Reihenfolge)
- → Q2-Q4 2026 sollte deutlich besser werden mit jetzt korrektem 4-Pack-Stack

## Speicher-Berechnung (komplettes System)

### Nutzbare Kapazität nach SoC-Fenster
| SoC-Fenster | DOD | Nutzbar AC | Zyklen | Effektive Lebensdauer |
|---|---|---|---|---|
| 0–100% | 100% | 31,0 kWh | 3.000 | 8 J zyklisch |
| **5–95%** (aktuell ESS) | 90% | 27,9 kWh | 5.000 | 14 J |
| **10–95%** (Sweet Spot) | 85% | 26,3 kWh | 6.500 | ~17 J (kal.-limitiert) |
| 15–90% | 75% | 23,3 kWh | 8.000 | ~17 J (kal.-limitiert) |
| 20–80% | 60% | 18,6 kWh | 12.000+ | ~17 J (kal.-limitiert) |

### Effizienz
- Round-Trip AC→AC: **86%** (gemessen)
- LFP-Zellen intern: ~98%
- MultiPlus-II AC↔DC: ~93% jeweils

### Maximaler Lade-/Entladestrom
- Pytes (4-Pack korrekt): 250 A Charge / 400 A Discharge
- JK-BMS (EVE): 144 A Charge / 150 A Discharge
- **Aggregator (kapazitätsgewichtet):** 330 A Charge (DVCC-Cap) / 249 A Discharge
- **Real-Engpass:** 3× MultiPlus = ~7,2 kW AC kontinuierlich

## Optimierungs-Empfehlungen (priorisiert nach Hebel)

### 🔴 1. mTec Wärmepumpe via Modbus TCP steuern (300-500 €/Jahr)
- WP läuft bei EG-Überschuss aus Gemeinschaft (10,80 ct vs 26 ct WEB)
- PV-Mittagsspitzen im Winter mitnehmen (auch wenn nur ~5 kWh/Tag) → Estrich vorladen
- Schreibe Reg 12 = 1 für "External Heat Request" (SG-Ready-Ersatz)
- Schreibe Reg 403 = 3 für DHW-Boost bei PV-Überschuss
- Logik: Outdoor-Temp + Soll-Raumtemp + PV-Forecast + EG-Verfügbarkeit

### 🔴 2. EG-Bezug aktiv nutzen (150-380 €/Jahr)
- Aktuell hauptsächlich Einspeiser in EG (April: 230 kWh in EG, nur 85 kWh aus EG)
- Im Winter wird sich Verhältnis umdrehen → mehr aus EG beziehen

### 🔴 3. Pytes-Stack stabil halten (~250 €/Jahr) — heute 26.04.2026 behoben
- War nur 1 von 4 Packs aktiv → nur 5 statt 20 kWh nutzbar
- Verifikation: `dbus -y com.victronenergy.battery.socketcan_can0 /System/NrOfModulesOnline GetValue` muss `4` zeigen
- Bei nächstem Pytes-Aufhänger: NIE einzeln rebooten — siehe Reboot-Reihenfolge oben

### 🟠 4. ESS SoC-Mindestwert 5% → 10-15%
- 5% physisch ist nahe LFP-Schadensbereich
- 10% Sweet Spot für Lebensdauer + Reserve

### 🟠 5. DVCC max charge V 56,0 → 55,2 V (LFP-Lebensdauer)
- 3,45 V/Zelle = LFP-Standard
- Verdoppelt Lebensdauer ggü. 56,0 V Dauerlast

### 🟡 6. ESS Sustain Voltage 49 → 51 V (in VEConfigure)
- 51 V = 3,19 V/Zelle ≈ 15% SoC, passend zu SoC-Min 15%

### 🟡 7. Lastmanagement Wallbox + WP (50-200 €/Jahr)
- Top-10 Peak-Events alle 17-22 Uhr im Winter
- Cerbo-Lastmanagement aktivieren

### ❌ Was sich NICHT lohnt
- **EG-Morgenstrategie (06-10 Uhr)**: Du speist nur 84 kWh/Jahr in 6-10 Uhr ein → 0,84 €/Jahr Mehrerlös
- **Tarifwechsel zu aWATTar/smartENERGY**: WEB Grünstrom mit 15,5 ct ist konkurrenzfähig
- **Akku vergrößern**: 36 kWh reichen im Sommer komplett, im Winter hilft mehr Speicher nichts (PV liefert kaum)

### Realistisches Gesamt-Sparpotenzial: 500-700 €/Jahr
(von aktuell 1.700 € auf 1.000-1.200 € Stromrechnung)

## Site-Daten (Stammdaten)

- **Anlagennummer**: 9000011942 (WEB)
- **Zählpunkt Bezug**: AT0020000000000000000000020518244 (Zählernr. 178210339744)
- **Zählpunkt Einspeisung**: AT0020000000000000000000100244504
- **VRM Portal ID**: dca63237f73d (Installation 404859, https://vrm.victronenergy.com/installation/404859)
- **PV Engpassleistung**: 19 kWp
- **EG-Mitglieds-Nr**: RC107217-000155 ("G5 Nord")

---

# Software — Implementierungsdetails & bekannte Probleme

## Versionierung

- Version steht in `package.json` UND `ems-client/db_handler.py` (beide müssen synchron sein)
- Bei jedem Release BEIDE bumpen: `"version": "X.Y.Z"` und `VERSION = "X.Y.Z"`

## NRG Kick Gen2 — Modbus TCP (10.10.10.52)

### Register Map (kritisch)
| Reg | Name | Typ | Skala | R/W |
|---|---|---|---|---|
| 194 | Charging amperage setpoint | uint16 | ×0.1 A | W |
| 195 | Charging pause | uint16 | 0=run, 1=pause | W |
| 198 | Phase count max | uint16 | 1-3 | W |
| 203 | Session energy | uint32 LSW-first | Wh | R |
| 210 | Combined active power | int32 LSW-first | ×0.001 W | R |
| 217-219 | L1/L2/L3 voltage | uint16 | ×0.01 V | R |
| 220-222 | L1/L2/L3 current | uint16 | ×0.001 A | R |
| 224-226 | L1/L2/L3 active power | int32 LSW-first | ×0.001 W | R |
| 251 | Charging status | uint16 | enum | R |
| 252 | Charge permission | uint16 | | R |

### Status-Mapping (Register 251) — VERIFIZIERT
```
0 → "A"   # Unbekannt/Standby
1 → "A"   # Standby — kein Fahrzeug
2 → "B"   # Connected — Fahrzeug verbunden, ladet NICHT
3 → "C"   # Charging — Fahrzeug ladet aktiv!
6 → "F"   # Fehler
7 → "B"   # Wake-Up — wie Connected behandeln
```
**Achtung**: Früherer Bug hatte 4/5→C (falsch). 3→C ist korrekt und verifiziert.

### Int32 LSW-first Byte Order
Register 210 (Power) und 203 (Session Energy) sind 32-bit, **Low Word zuerst**:
```python
lsw = regs[0]   # Low Word (bits 0-15)
msw = regs[1]   # High Word (bits 16-31)
value_raw = lsw | (msw << 16)
value_w = value_raw / 1000
```

### NRG Kick Watchdog — KRITISCH (Root Cause 5-Min-Stop)
Der NRG Kick Gen2 hat einen internen Session-Watchdog (~5 Minuten). Dieser
resettet sich NUR wenn Register 195 (Pause) geschrieben wird — nicht allein
durch Register 194 (Strom-Setpoint). Wenn Register 195 nicht regelmäßig
beschrieben wird, pausiert der NRG Kick nach ca. 5 Minuten automatisch.

**Fix (v1.0.41)**: Im Sofort-Modus ("now") wird Register 195 jeden Zyklus (10s)
als Heartbeat geschrieben, auch wenn sich der Enable-Status nicht ändert.
Code: `_set_charging()` → Heartbeat-Block am Ende.

### Phasen (NIE umschalten bei Zoe)
- Register 198 = phase_count_max: NIEMALS zur Laufzeit ändern!
- Zoe verliert bei Phasenwechsel die Session und startet nicht neu
- Immer 3-phasig konfigurieren, Fix in wald-ems.yaml: `phases: 3`

## Renault Zoe — Bekannte Charakteristika

### Mindest-Ladestrom (KRITISCH)
- IEC 61851 sagt 6A Minimum, aber Zoe hat einen praktischen Schwellwert
- Unter ~8.5A fällt der Power Factor auf 0.05–0.39 (Zombie-Mode)
- **Konfiguration: `min_current: 9` in wald-ems.yaml** (9A = sicherer Betrieb)
- Bei 9A: PF ~0.85 → echte Leistung ~5.3 kW (nicht 6.21 kW apparent)

### Zoe Strom-Sweep (gemessen):
| Strom | Echte Leistung | PF |
|---|---|---|
| 6.0 A | ~240 W | 0.05 |
| 7.0 A | ~240 W | 0.05 |
| 8.0 A | ~1.9 kW | 0.39 |
| 8.5 A | ~2.7 kW | 0.52 |
| 9.0 A | ~4.7–5.3 kW | 0.83–0.85 |
| 16.0 A | ~10.8 kW | ~1.0 |

### Zombie-Mode
Wenn Zoe mehrfach unterbrochen wird (Pause-Toggle, Modbus-Glitch, etc.),
"schläft" sie ein: Status bleibt B (verbunden), kein Strom wird gezogen.
Einzige Lösung: CP-Signal-Wechsel durch Pause-Toggle (kurz ausschalten).

**Zombie-Wake-Up im Code** (`core/loadpoint.py`):
- Erkennung: `status == "B" AND power < 50W AND enabled == True`
- Timeout: 60s bei Sofort-Mode, 300s bei PV-Mode
- Aktion: `charger.enable(False)` → 1s sleep → `charger.enable(True)`
- Max einmal alle 10 Minuten (verhindert Loop)
- Wenn das nicht hilft: Kabel kurz ausstecken!

## Loadpoint-Regelung (core/loadpoint.py)

### Hysterese-Parameter
```yaml
enable_threshold_w: 4000   # 4 kW müssen verfügbar sein zum Starten (PV-Mode)
enable_delay_s: 20         # 20s warten bevor Enable (PV-Mode)
disable_threshold_w: 0     # Bei negativem Überschuss stoppen
disable_delay_s: 300       # 5 Min Wolken-Puffer (PV-Mode)
```

### available_w Berechnung (site.py) — Stabilisierungs-Trick
Wenn LP aktiviert ist (enabled + target > 0), nutze SOLL-Leistung statt
gemessene Leistung. Das verhindert Oszillation während des Ramp-Ups:
```python
def _lp_power_for_calc(lp):
    if lp._last_written_enabled and lp._target_current_a > 0:
        return lp._target_current_a * 230 * lp.phases  # Soll-Leistung
    return lp._charging_power_w  # Ist-Leistung (wenn aus)
```

### Auto-vor-Batterie (battery_redirect)
Wenn Hausbatterie lädt UND SoC ≥ priority_soc: Batterie-Ladeleistung zu
available_w addieren. Auto lädt aus PV, Batterie stoppt nicht komplett.
```python
if self.battery_power_w > 50:  # Batterie lädt
    if priority_soc <= 0 or battery_soc >= priority_soc:
        battery_redirect_w = self.battery_power_w
```

### Solar/Grid-Tracking
Jeder Ladezyklus berechnet Solar-Anteil:
```python
if charging_power > 50 and grid_import > 0:
    grid_share = min(1.0, grid_import / charging_power)
    solar_share = 1.0 - grid_share
else:
    solar_share = 1.0  # Einspeisung → 100% Solar
```

## Victron Venus OS — Modbus TCP (10.10.10.70, unit=100)

### Kritische Register (verifiziert)
| Register | Name | Einheit | Vorzeichen |
|---|---|---|---|
| 820–822 | Grid L1/L2/L3 Power | W | + = Import |
| 842 | Battery Power | W | + = Laden |
| 843 | Battery SoC | % | |
| 850 | PV DC Power (MPPT total) | W | |
| 808–810 | AC-Out L1/L2/L3 | W | Verbrauch |
| 811–813 | AC-In L1/L2/L3 | W | |
| 817–819 | AC-Out Consumption L1/L2/L3 | W | |

### Grid-Berechnung
```python
# Grid Power: Summe L1+L2+L3 (positiv = Import, negativ = Export)
grid_w = reg_820 + reg_821 + reg_822
# PV: MPPT DC + AC-gekoppelte PV (Hoymiles, SMA)
pv_w = reg_850 + (reg_808 + reg_809 + reg_810)  # vereinfacht
```

## Bekannte Bugs & Fixes (Chronologie)

| Version | Bug | Fix |
|---|---|---|
| ≤v1.0.19 | NRG Status 3→"B" statt "C" | Status-Map korrigiert: 3→"C" |
| v1.0.25 | 5-Min-Stop durch Watchdog | Strom-Register 194 jeden Zyklus schreiben |
| v1.0.26 | Watchdog detection | `charger.enabled()` liest Pause-Register, re-enable wenn False |
| v1.0.31 | requests fehlt für Renault | `requirements.txt` ergänzt |
| v1.0.34 | Auto-vs-Batterie | battery_power_w zu available_w addieren wenn Batterie lädt |
| v1.0.36 | available_w Oszillation | Soll-Leistung statt Ist-Leistung für laufende LPs |
| v1.0.37 | Zoe Zombie-Mode | Wake-Up Toggle nach 300s |
| v1.0.41 | 5-Min-Sofort-Stop (neu!) | Heartbeat: Reg 195 jeden Zyklus in Sofort-Mode; Zombie-Timer 60s für "now" |
| v1.0.42 | Ladesteuerung von Wald Energycontrol portiert | Reife Loadpoint-Logik übernommen (siehe unten) |
| v1.0.43 | Treiber-Robustheit portiert (NRG Kick + Renault) | Modbus-Lesefehler ≠ 0; dynamische Gigya-Keys (siehe unten) |

### v1.0.43 — Treiber-Robustheit von Wald Energycontrol übernommen
v1.0.42 brachte das reife Loadpoint-"Gehirn", aber die Regelung ist nur so gut
wie die **Rohdaten** der Treiber. Zwei Treiber hinkten beim Bruder-Pi noch nach:

**NRG Kick (`drivers/nrgkick/modbus.py`) — Modbus-Lesefehler ≠ Nullwert.**
Alte Version: `_read_reg()` gab bei Modbus-Fehler **0.0** zurück. Folge:
- `status()`: `int(0)` → **"A" (nicht verbunden)** → System denkt Auto ausgesteckt →
  Session endet, Laden stoppt bei jedem Modbus-Glitch.
- `current_power()`/`currents()`: **0 A** → "kein Strom" → falsche Solar-Berechnung,
  evtl. Zombie-Trigger.
Fix: `_read_reg_nullable()` gibt bei Fehler `None` → `status()` behält letzten Status,
`currents()` fällt auf Cache zurück. **Wahrscheinliche Hauptursache der Ladeabbrüche.**

**Renault (`drivers/vehicle/renault.py`) — dynamische Gigya-Keys.**
Alte Version: `GIGYA_API_KEY` hart codiert + `_soc = 0` bei Fehler. Renault rotiert
die Keys → Login schlägt fehl → SoC bleibt 0 → **kein Stopp bei target_soc**, nur
grober kWh-Cap. Fix (evcc-Stil):
- `_load_dynamic_keys()` holt aktuelle Keys aus Renaults S3-KeyStore (24h-Cache,
  Fallback auf hart codierte Werte), frischerer Fallback-Key (Stand 2026-05).
- `_soc` initial `None` (nicht 0) → Loadpoint weiß "SoC unbekannt" statt "Auto leer".
- SoC-Sanity-Filter (0%-Aussetzer ignoriert), Stale-Check (>60min → None).
- Schnelleres Polling während Ladung (120s statt 300s) → Target-SoC-Stop greift zügiger.
- POST-Login (statt GET) + detailliertes Auth-Fehler-Logging ins Dashboard.
- `rv._db = db` in main.py → Renault-Auth-Fehler erscheinen im Dashboard-Log.

Damit ist der komplette **Lade-Pfad** (Loadpoint + NRG-Kick-Treiber + Renault-Treiber
+ site.pv_surplus_w) mit Wald Energycontrol gleichgezogen. Verbleibende Unterschiede
zu WEC sind reine **Feature-Extras** (Wärmepumpe/KEBA, Consumer-Aufschlüsselung,
ioBroker, EG-Tarif, Pytes-Pack-Monitor, ev_priority_pct, Peak-Avoidance) — nicht
lade-relevant.

### 5-Min-Stop — Warum v1.0.25 nicht dauerhaft half
v1.0.25 schrieb Register 194 (Strom) jeden Zyklus. Der NRG Kick Watchdog
resettet sich aber nur durch Register 195 (Pause). Da Register 195 nur bei
Zustandswechseln geschrieben wurde (on-change), timer der NRG Kick intern ab.
Fix v1.0.41: Heartbeat schreibt Register 195 in jedem Zyklus bei "now"-Mode.

### v1.0.42 — Ladesteuerung von Wald Energycontrol übernommen
Wald EMS (Bruder-Pi) lief auf der alten, einfacheren Loadpoint-Logik. Die
ausgereifte Regelung aus **Wald Energycontrol** (v1.8.6, Benjis Zuhause) wurde
nach `core/loadpoint.py` portiert und mit den v1.0.41-Fixes gemerged. Neu:

- **Session-basierte SoC-Estimation** (evcc-style): Beim Plug-in wird der SoC als
  Baseline gesnapshotet, Wallbox-Energie monoton aufaddiert. `estimated =
  max(api_soc, session_start + delivered/battery*100)`. Robust gegen den trägen
  Renault-Cloud-Lag (Cloud-SoC ist immer hinterher → Wallbox-Term gewinnt).
- **kWh-Safety-Cap**: Stoppt bei `battery_kwh * session_limit_factor` (Default 95%),
  falls die SoC-Estimation/Cloud-API ausfällt. Ohne SoC-Daten konservativer Cap.
- **Plug-out-Hysterese (60s)**: Status-A-Glitches (Modbus-Watchdog-Reset) zerstören
  nicht mehr den Session-Start-SoC. Erst 60s konstantes A = echtes Abstecken.
- **Symmetrischer Watchdog**: `_set_charging()` prüft JEDEN Zyklus ob der reale
  Charger-Zustand dem Soll entspricht — und schreibt bei Abweichung in BEIDE
  Richtungen neu (vorher: Stop nur einmal → Auto lud heimlich auf 100% weiter).
- **Session-Persistenz in DB** (`state`-Tabelle, alle 30s): Service-Restart mitten
  in laufender Session stellt Start-SoC + gelieferte Energie wieder her.
- **`min_pv`-Modus mit echtem PV-Surplus**: nutzt `pv_surplus_w` (PV − Hausgrundlast)
  statt `available_w`, damit der LP bei wenig PV NICHT die Hausbatterie leerzieht.
- **`zombie_wakeup_enabled`**: CP-Toggle-Wake-Up; für Renault automatisch AUS
  (in `main.py` beim Vehicle-Zuordnen), da die Zoe sonst die Session verliert.
- v1.0.41-Fixes erhalten: NRG-Kick-Heartbeat (Reg 195 jeden Zyklus im "now"-Mode)
  + mode-abhängiger Zombie-Timer (60s now / 300s pv).

**Neue Abhängigkeiten:** `db_handler.get_state/set_state` (JSON-State-Tabelle),
`site.pv_surplus_w` (an `lp.update()` durchgereicht), `config.py` reicht neue
Loadpoint-Optionen durch (`session_limit_factor`, `zombie_wakeup_enabled`,
Hysterese-Params, `priority`, `circuit_id`).

**Bruder-Pi Config-Empfehlung** (`wald-ems.yaml`): `min_current: 9` (war 13 → harte
9-kW-Untergrenze), `priority_soc: 20` (Speicher-Schutz beim Wolkenpuffer), Vehicle
mit `battery_kwh: 40` (aktiviert SoC-Estimation + Stop bei target_soc).

## Installation auf neuem Raspberry Pi

### Voraussetzungen
- Raspberry Pi 4 oder 5, mind. 4 GB RAM empfohlen
- Raspberry Pi OS Lite (64-bit, Debian Bookworm)
- LAN-Kabel, feste IP konfigurieren (DHCP-Reservation im Router)
- SSH-Zugang funktioniert

### Schnell-Installation (ein Befehl)
```bash
curl -fsSL https://raw.githubusercontent.com/benjiwald/wald-ems/main/scripts/install.sh | sudo bash
```
Der Installer:
1. Installiert Node.js 20 + Python 3 + System-Abhängigkeiten
2. Lädt Pre-built Release von GitHub (oder klont + baut lokal als Fallback)
3. Erstellt `ems` System-User in `/opt/ems/`
4. Kompiliert `better-sqlite3` für ARM neu (Pi-spezifisch, dauert ~2 min)
5. Erstellt `wald-ems.yaml` aus Beispiel-Config
6. Aktiviert systemd-Services `wald-ems` + `wald-ems-client`

### Manuelle Installation (wenn kein GitHub Release)
```bash
# 1. Voraussetzungen
sudo apt-get update && sudo apt-get install -y python3 python3-venv nodejs npm git build-essential

# 2. Code kopieren (vom Entwickler-PC via rsync)
sudo mkdir -p /opt/ems
sudo rsync -avz --exclude node_modules --exclude .next --exclude venv \
  "/pfad/zum/lokalen/wald-ems/" pi@10.10.10.22:/opt/ems/

# 3. Auf dem Pi: Build
ssh pi@10.10.10.22
cd /opt/ems
npm install
npm run build

# 4. Python venv
python3 -m venv /opt/ems/venv
/opt/ems/venv/bin/pip install -r /opt/ems/ems-client/requirements.txt

# 5. Config anpassen
cp /opt/ems/wald-ems.yaml.example /opt/ems/wald-ems.yaml
nano /opt/ems/wald-ems.yaml

# 6. Services installieren
sudo cp /opt/ems/scripts/wald-ems*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wald-ems wald-ems-client
```

### Konfiguration (wald-ems.yaml) — Heidenreichstein
```yaml
site:
  name: "Heidenreichstein"
  grid_limit_kw: 11
  buffer_w: 100
  priority_soc: 0          # Batterie-Vorrang aus (Auto-vor-Batterie-Logik)
  grid_price_eur_kwh: 0.26
  feedin_price_eur_kwh: 0.07

meters:
  - name: "Victron System"
    type: victron_venus_system
    host: 10.10.10.70
    port: 502
    unit_id: 100

chargers:
  - name: "NRG Kick"
    type: nrgkick_modbus
    host: 10.10.10.52
    port: 502
    unit_id: 1

loadpoints:
  - name: "Einfahrt"
    charger: "NRG Kick"
    mode: pv
    min_current: 9      # Zoe: mind. 9A (unter 8.5A = Zombie-Mode)
    max_current: 16
    phases: 3           # Zoe: IMMER 3-phasig!
    target_soc: 90
    min_soc: 20

vehicles:
  - name: "Renault Zoe"
    manufacturer: renault
    vin: "VF1..."        # echte VIN eintragen
    battery_kwh: 40
    credentials:
      email: "..."
      password: "..."

database:
  path: /opt/ems/wald-ems.db
  retention_days: 30
```

### Nach der Installation prüfen
```bash
# Service-Status
sudo systemctl status wald-ems
sudo systemctl status wald-ems-client

# Logs live
sudo journalctl -u wald-ems-client -f

# Dashboard aufrufen
http://10.10.10.22:7777

# DB direkt prüfen
sqlite3 /opt/ems/wald-ems.db "SELECT key, substr(value,1,100) FROM state"
```

## Update-Prozess

### Über das Dashboard (einfach)
Einstellungen → Software-Update → "Jetzt aktualisieren"

### Manuell auf dem Pi
```bash
sudo /opt/ems/scripts/update.sh
```

### Code-Änderung vom Entwickler-PC übertragen
```bash
# Nur geänderte Dateien (schnell)
rsync -avz --exclude node_modules --exclude .next --exclude venv --exclude "*.pyc" \
  "/Users/benjamin/BW Dropbox/BW Team/Server/Workspace/2026/_Coding/20260406 Wald EMS/ems-client/" \
  pi@10.10.10.22:/opt/ems/ems-client/

# Python-Client neu starten (kein Build nötig)
ssh pi@10.10.10.22 "sudo systemctl restart wald-ems-client"

# Bei Dashboard-Änderungen: Build + Neustart
rsync -avz [Dashboard-Dateien] pi@10.10.10.22:/opt/ems/
ssh pi@10.10.10.22 "cd /opt/ems && npm run build && sudo systemctl restart wald-ems"
```

### Bruder-Pi (gleiche Hardware, andere IP)
Gleiche Prozedur, IP-Adresse anpassen. Config-Datei (`wald-ems.yaml`) ist
standortspezifisch und wird beim Update NICHT überschrieben.

## Troubleshooting

### Wallbox lädt nicht
1. Status im Dashboard prüfen (A=kein Auto, B=verbunden, C=ladet)
2. Modus prüfen: "now" = Sofort, "pv" = nur PV-Überschuss
3. Zoe Zombie? → Kabel ausstecken/einstecken
4. NRG Kick Logs: `sudo journalctl -u wald-ems-client | grep -i nrg`
5. Modbus-Test: `python3 -c "from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient('10.10.10.52'); c.connect(); print(c.read_holding_registers(251,1,slave=1).registers)"`

### Alle 5 Minuten Pause → Fix
Ab v1.0.41 behoben. Bei älteren Versionen:
- Root Cause: NRG Kick Watchdog resettet nur bei Reg 195 Write
- Workaround alt: Code manuell in `_set_charging()` Heartbeat einbauen

### Python-Client startet nicht
```bash
sudo journalctl -u wald-ems-client -n 50
# Häufig: wald-ems.yaml fehlt oder hat Syntaxfehler
# Prüfen: python3 -c "import yaml; yaml.safe_load(open('/opt/ems/wald-ems.yaml'))"
```

### Dashboard lädt nicht (Port 7777)
```bash
sudo systemctl status wald-ems
# better-sqlite3 Fehler? → ARM-Rebuild:
cd /opt/ems/dashboard && npm install better-sqlite3 --no-save
sudo systemctl restart wald-ems
```

### Victron-Daten fehlen (alle 0)
- VenusOS Modbus aktiv? → Cerbo GX → Einstellungen → Services → Modbus TCP → Ein
- Unit ID 100 prüfen: `python3 -c "from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient('10.10.10.70'); c.connect(); print(c.read_holding_registers(820,3,slave=100).registers)"`

### Renault Zoe SoC fehlt
- Gigya/Kamereon API: Credentials in wald-ems.yaml prüfen
- Renault MY Renault App muss aktiv sein
- Rate Limit: SoC wird nur alle 5 Minuten abgefragt (Cache)
- Logs: `sudo journalctl -u wald-ems-client | grep -i renault`

# Wald EMS

Lokales Energiemanagementsystem fuer Raspberry Pi. Inspiriert von [evcc](https://evcc.io).

Laeuft komplett lokal — kein Cloud-Account, kein Internet noetig.

## Features

- PV-Ueberschussladen (4 Modi: Aus, Sofort, Min+PV, PV)
- Energiefluss-Visualisierung (Grid, PV, Batterie, Verbrauch)
- Intelligente Lastverteilung
- Ladesession-Tracking
- PV-Prognose (forecast.solar)
- Dynamischer Tarif (aWATTar)
- Modbus TCP (Victron, NRG Kick, SMA, Fronius, Shelly)
- Web-Dashboard auf Port 3000

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/benjiwald/wald-ems/main/scripts/install.sh | sudo bash
```

## Konfiguration

Nach der Installation: `/opt/ems/wald-ems.yaml` bearbeiten:

```yaml
site:
  name: "Mein Zuhause"
  grid_limit_kw: 11

meters:
  - name: "Victron System"
    type: victron_venus_system
    host: 192.168.1.70
    port: 502
    unit_id: 100

chargers:
  - name: "Wallbox"
    type: nrgkick_modbus
    host: 192.168.1.84
    port: 502
    unit_id: 1

loadpoints:
  - name: "Garage"
    charger: "Wallbox"
    mode: pv
    min_current: 6
    max_current: 16
    phases: 1
```

Dann Services neu starten:

```bash
sudo systemctl restart wald-ems-client
```

Dashboard oeffnen: `http://<raspberry-ip>:3000`

## Architektur

```
┌─────────────────────────────────────────┐
│           Raspberry Pi                   │
│                                          │
│  ┌──────────────┐  ┌──────────────────┐ │
│  │  Next.js      │  │  Python Client   │ │
│  │  Dashboard    │  │  (Modbus/Control)│ │
│  │  :3000        │  │                  │ │
│  └──────┬───────┘  └────────┬─────────┘ │
│         │    SQLite          │           │
│         └────────┬───────────┘           │
│                  │                       │
│           wald-ems.db                    │
└─────────────────────────────────────────┘
         │
    Modbus TCP
         │
┌────────┴────────┐
│ Victron / NRG   │
│ Kick / SMA ...  │
└─────────────────┘
```

## Services

```bash
# Status
sudo systemctl status wald-ems          # Dashboard
sudo systemctl status wald-ems-client   # Modbus Client

# Logs
journalctl -u wald-ems-client -f

# Neustart
sudo systemctl restart wald-ems-client
```

## Entwicklung

```bash
# Dashboard
npm install
npm run dev   # http://localhost:3000

# Python Client
cd ems-client
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
WALD_EMS_CONFIG=../wald-ems.yaml python main.py
```

## Basiert auf

[Wania EMS](https://github.com/benjiwald/Wania-EMS) — Cloud-basiertes EMS fuer Elektrikerbetriebe.
Wald EMS ist die lokale Single-Site-Version fuer den Eigengebrauch.

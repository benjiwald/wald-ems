#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# Wald EMS — Installer fuer Raspberry Pi / Linux
# Nutzung: curl -fsSL https://raw.githubusercontent.com/benjiwald/wald-ems/main/scripts/install.sh | sudo bash
# ──────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_DIR="/opt/ems"
REPO="benjiwald/wald-ems"
BRANCH="main"

echo -e "${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║          Wald EMS Installer          ║"
echo "║    Lokales Energiemanagement         ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

# Root check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Bitte als root ausfuehren:${NC}"
    echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/scripts/install.sh | sudo bash"
    exit 1
fi

# ── 1. System-Dependencies ──────────────────────────────────────────

echo -e "${YELLOW}[1/7] System-Pakete installieren...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl git build-essential

# Node.js 20 LTS
if ! command -v node &>/dev/null || [[ $(node -v | cut -d. -f1 | tr -d 'v') -lt 18 ]]; then
    echo -e "${YELLOW}  Node.js 20 LTS installieren...${NC}"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi

echo -e "${GREEN}  Node $(node -v) | Python $(python3 --version | cut -d' ' -f2) | npm $(npm -v)${NC}"

# ── 2. User und Verzeichnis ─────────────────────────────────────────

echo -e "${YELLOW}[2/7] Benutzer und Verzeichnis erstellen...${NC}"
if ! id -u ems &>/dev/null; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /bin/bash ems
    echo -e "  User 'ems' erstellt"
fi
mkdir -p "$INSTALL_DIR"

# ── 3. Repository klonen ────────────────────────────────────────────

echo -e "${YELLOW}[3/7] Wald EMS herunterladen...${NC}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "  Git-Repo existiert bereits — aktualisiere..."
    cd "$INSTALL_DIR"
    git fetch origin main
    git reset --hard origin/main
else
    echo -e "  Klone Repository..."
    # Direkt nach /opt/ems klonen (inkl. .git)
    rm -rf /tmp/wald-ems-clone
    git clone --depth 1 -b "$BRANCH" "https://github.com/${REPO}.git" /tmp/wald-ems-clone

    # Alles nach INSTALL_DIR verschieben (inkl. versteckte Dateien)
    shopt -s dotglob
    cp -a /tmp/wald-ems-clone/* "$INSTALL_DIR/" 2>/dev/null || true
    cp -a /tmp/wald-ems-clone/.git "$INSTALL_DIR/" 2>/dev/null || true
    cp -a /tmp/wald-ems-clone/.gitignore "$INSTALL_DIR/" 2>/dev/null || true
    shopt -u dotglob
    rm -rf /tmp/wald-ems-clone
fi

echo -e "${GREEN}  $(cd "$INSTALL_DIR" && git log -1 --format='Commit: %h (%ci)')${NC}"

# ── 4. Dashboard bauen ──────────────────────────────────────────────

echo -e "${YELLOW}[4/7] Dashboard bauen (npm install + next build)...${NC}"
cd "$INSTALL_DIR"
npm install 2>&1 | tail -3
npm run build 2>&1 | tail -5

# Standalone-Verzeichnis vorbereiten
rm -rf "$INSTALL_DIR/dashboard"
mkdir -p "$INSTALL_DIR/dashboard/.next"
cp -a "$INSTALL_DIR/.next/standalone/." "$INSTALL_DIR/dashboard/"
cp -a "$INSTALL_DIR/.next/static" "$INSTALL_DIR/dashboard/.next/static"
[ -d "$INSTALL_DIR/public" ] && cp -a "$INSTALL_DIR/public" "$INSTALL_DIR/dashboard/public"

echo -e "${GREEN}  Dashboard gebaut (standalone)${NC}"

# ── 5. Python venv ──────────────────────────────────────────────────

echo -e "${YELLOW}[5/7] Python-Umgebung einrichten...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
    echo -e "  Neues venv erstellt"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/ems-client/requirements.txt"
echo -e "${GREEN}  Python-Abhaengigkeiten installiert${NC}"

# ── 6. Konfiguration ────────────────────────────────────────────────

echo -e "${YELLOW}[6/7] Konfiguration + Systemd Services...${NC}"

# Config-Datei nur kopieren wenn noch keine existiert
if [ ! -f "$INSTALL_DIR/wald-ems.yaml" ]; then
    cp "$INSTALL_DIR/wald-ems.yaml.example" "$INSTALL_DIR/wald-ems.yaml"
    echo -e "${YELLOW}  wald-ems.yaml erstellt — BITTE ANPASSEN!${NC}"
else
    echo -e "  wald-ems.yaml existiert — wird nicht ueberschrieben"
fi

# SQLite Migrations (erstellt DB falls noetig)
if [ -f "$INSTALL_DIR/migrations/001_schema.sql" ]; then
    DB_PATH=$(grep -oP 'path:\s*\K.*' "$INSTALL_DIR/wald-ems.yaml" 2>/dev/null || echo "./wald-ems.db")
    # Relativen Pfad aufloesen
    if [[ "$DB_PATH" == ./* ]]; then
        DB_PATH="$INSTALL_DIR/${DB_PATH#./}"
    fi
    echo -e "  Datenbank: $DB_PATH"
fi

# Systemd Services installieren
cp "$INSTALL_DIR/scripts/wald-ems.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/wald-ems-client.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable wald-ems wald-ems-client
echo -e "${GREEN}  Systemd Services aktiviert${NC}"

# ── 7. Berechtigungen und Start ─────────────────────────────────────

echo -e "${YELLOW}[7/7] Berechtigungen setzen und starten...${NC}"

# ems-User darf git pull (fuer Updates aus dem Dashboard)
chown -R ems:ems "$INSTALL_DIR"

# ems-User sudo-Recht fuer update.sh (fuer den Update-Button im Dashboard)
SUDOERS_FILE="/etc/sudoers.d/wald-ems"
if [ ! -f "$SUDOERS_FILE" ]; then
    echo "ems ALL=(ALL) NOPASSWD: $INSTALL_DIR/scripts/update.sh" > "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
    echo -e "  Sudoers-Regel fuer Update-Button erstellt"
fi

systemctl restart wald-ems-client || systemctl start wald-ems-client
systemctl restart wald-ems || systemctl start wald-ems

# Kurz warten und Status pruefen
sleep 3
CLIENT_STATUS=$(systemctl is-active wald-ems-client 2>/dev/null || echo "failed")
DASH_STATUS=$(systemctl is-active wald-ems 2>/dev/null || echo "failed")

# ── Fertig ───────────────────────────────────────────────────────────

LOCAL_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════╗"
echo -e "║  Wald EMS erfolgreich installiert!                 ║"
echo -e "╠═══════════════════════════════════════════════════╣"
echo -e "║                                                   ║"
echo -e "║  Dashboard:  http://${LOCAL_IP}:3000               ║"
echo -e "║  Config:     ${INSTALL_DIR}/wald-ems.yaml          ║"
echo -e "║                                                   ║"
echo -e "║  Client:     ${CLIENT_STATUS}                      ║"
echo -e "║  Dashboard:  ${DASH_STATUS}                        ║"
echo -e "║                                                   ║"
echo -e "║  Logs:    journalctl -u wald-ems-client -f        ║"
echo -e "║  Update:  Im Dashboard unter Einstellungen        ║"
echo -e "║           oder: sudo /opt/ems/scripts/update.sh   ║"
echo -e "║                                                   ║"
echo -e "╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Naechster Schritt:${NC}"
echo -e "  1. Config anpassen:  sudo nano ${INSTALL_DIR}/wald-ems.yaml"
echo -e "  2. Neu starten:      sudo systemctl restart wald-ems-client"
echo -e "  3. Dashboard oeffnen: http://${LOCAL_IP}:3000"

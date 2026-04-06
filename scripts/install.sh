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
RELEASE_URL="https://github.com/${REPO}/releases/latest/download/wald-ems.tar.gz"

echo -e "${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║          Wald EMS Installer          ║"
echo "║    Lokales Energiemanagement         ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Bitte als root:${NC}"
    echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/scripts/install.sh | sudo bash"
    exit 1
fi

# ── 1. System-Pakete ────────────────────────────────────────────────

echo -e "${YELLOW}[1/6] System-Pakete installieren...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl git build-essential

# Node.js 20 LTS (nur fuer die Runtime, kein Build noetig)
if ! command -v node &>/dev/null || [[ $(node -v | cut -d. -f1 | tr -d 'v') -lt 18 ]]; then
    echo -e "  Node.js 20 LTS installieren..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi

echo -e "${GREEN}  Node $(node -v) | Python $(python3 --version | cut -d' ' -f2)${NC}"

# ── 2. User und Verzeichnis ─────────────────────────────────────────

echo -e "${YELLOW}[2/6] Benutzer und Verzeichnis erstellen...${NC}"
if ! id -u ems &>/dev/null; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /bin/bash ems
    echo -e "  User 'ems' erstellt"
fi
mkdir -p "$INSTALL_DIR"

# ── 3. Pre-built Release herunterladen ──────────────────────────────

echo -e "${YELLOW}[3/6] Wald EMS herunterladen...${NC}"

# Versuche fertiges Release (von GitHub Actions gebaut)
if curl -fsSL -o /tmp/wald-ems.tar.gz "$RELEASE_URL" 2>/dev/null; then
    echo -e "  Pre-built Release gefunden — entpacke..."
    tar -xzf /tmp/wald-ems.tar.gz -C "$INSTALL_DIR"
    rm /tmp/wald-ems.tar.gz
    echo -e "${GREEN}  Release installiert${NC}"
else
    echo -e "${YELLOW}  Kein Release gefunden — klone Repository und baue lokal...${NC}"
    echo -e "${YELLOW}  (Dauert auf dem Pi 5-10 Minuten)${NC}"

    # Git clone
    if [ -d "$INSTALL_DIR/.git" ]; then
        cd "$INSTALL_DIR" && git fetch origin main && git reset --hard origin/main
    else
        rm -rf /tmp/wald-ems-clone
        git clone --depth 1 -b main "https://github.com/${REPO}.git" /tmp/wald-ems-clone
        cp -a /tmp/wald-ems-clone/. "$INSTALL_DIR/"
        rm -rf /tmp/wald-ems-clone
    fi

    # Lokal bauen (Fallback — langsam auf Pi)
    cd "$INSTALL_DIR"
    apt-get install -y -qq build-essential
    npm install
    npm run build

    mkdir -p "$INSTALL_DIR/dashboard/.next"
    cp -a "$INSTALL_DIR/.next/standalone/." "$INSTALL_DIR/dashboard/"
    cp -a "$INSTALL_DIR/.next/static" "$INSTALL_DIR/dashboard/.next/static"
    [ -d "$INSTALL_DIR/public" ] && cp -a "$INSTALL_DIR/public" "$INSTALL_DIR/dashboard/public"
fi

# better-sqlite3: Release ist fuer x86_64 gebaut.
# Auf ARM muss das native Module neu kompiliert werden.
ARCH=$(uname -m)
if [[ "$ARCH" != "x86_64" ]]; then
    echo -e "  Native Module fuer ${ARCH} kompilieren..."
    cd "$INSTALL_DIR/dashboard"
    rm -rf node_modules/better-sqlite3 node_modules/.better-sqlite3*
    npm install better-sqlite3 --no-save 2>&1 | tail -5
    echo -e "${GREEN}  better-sqlite3 fuer ${ARCH} kompiliert${NC}"
else
    echo -e "${GREEN}  x86_64 — pre-built binary passt${NC}"
fi

# Git-Repo auch holen (fuer Updates aus dem Dashboard)
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo -e "  Git-Repo fuer Updates einrichten..."
    cd "$INSTALL_DIR"
    git init
    git remote add origin "https://github.com/${REPO}.git"
    git fetch origin main --depth 1
    git reset --soft origin/main
fi

# ── 4. Python venv ──────────────────────────────────────────────────

echo -e "${YELLOW}[4/6] Python-Umgebung einrichten...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/ems-client/requirements.txt"
echo -e "${GREEN}  Python-Abhaengigkeiten installiert${NC}"

# ── 5. Konfiguration + Services ─────────────────────────────────────

echo -e "${YELLOW}[5/6] Konfiguration + Systemd Services...${NC}"

if [ ! -f "$INSTALL_DIR/wald-ems.yaml" ]; then
    cp "$INSTALL_DIR/wald-ems.yaml.example" "$INSTALL_DIR/wald-ems.yaml"
    echo -e "${YELLOW}  wald-ems.yaml erstellt — BITTE ANPASSEN!${NC}"
else
    echo -e "  wald-ems.yaml existiert — wird nicht ueberschrieben"
fi

cp "$INSTALL_DIR/scripts/wald-ems.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/wald-ems-client.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable wald-ems wald-ems-client

# Sudoers fuer Update-Button
SUDOERS_FILE="/etc/sudoers.d/wald-ems"
if [ ! -f "$SUDOERS_FILE" ]; then
    echo "ems ALL=(ALL) NOPASSWD: $INSTALL_DIR/scripts/update.sh" > "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
fi

echo -e "${GREEN}  Services aktiviert${NC}"

# ── 6. Starten ──────────────────────────────────────────────────────

echo -e "${YELLOW}[6/6] Berechtigungen setzen und starten...${NC}"
chown -R ems:ems "$INSTALL_DIR"

systemctl restart wald-ems-client 2>/dev/null || systemctl start wald-ems-client
systemctl restart wald-ems 2>/dev/null || systemctl start wald-ems

sleep 3
CLIENT_STATUS=$(systemctl is-active wald-ems-client 2>/dev/null || echo "failed")
DASH_STATUS=$(systemctl is-active wald-ems 2>/dev/null || echo "failed")

LOCAL_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════╗"
echo -e "║  Wald EMS erfolgreich installiert!                 ║"
echo -e "╠═══════════════════════════════════════════════════╣"
echo -e "║                                                   ║"
echo -e "║  Dashboard:  http://${LOCAL_IP}:7777               ║"
echo -e "║  Config:     ${INSTALL_DIR}/wald-ems.yaml          ║"
echo -e "║                                                   ║"
echo -e "║  Client:     ${CLIENT_STATUS}                      ║"
echo -e "║  Dashboard:  ${DASH_STATUS}                        ║"
echo -e "║                                                   ║"
echo -e "║  Update:  Im Dashboard unter Einstellungen        ║"
echo -e "║           oder: sudo /opt/ems/scripts/update.sh   ║"
echo -e "║                                                   ║"
echo -e "╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Naechster Schritt:${NC}"
echo -e "  1. Config anpassen:  sudo nano ${INSTALL_DIR}/wald-ems.yaml"
echo -e "  2. Neu starten:      sudo systemctl restart wald-ems-client"
echo -e "  3. Dashboard oeffnen: http://${LOCAL_IP}:7777"

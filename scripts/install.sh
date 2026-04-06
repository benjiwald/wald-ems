#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# Wald EMS — Installer für Raspberry Pi / Linux
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
    echo -e "${RED}Bitte als root ausfuehren: sudo bash install.sh${NC}"
    exit 1
fi

# ── 1. System-Dependencies ──────────────────────────────────────────

echo -e "${YELLOW}[1/7] System-Pakete installieren...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl git

# Node.js 20 LTS
if ! command -v node &>/dev/null || [[ $(node -v | cut -d. -f1 | tr -d 'v') -lt 18 ]]; then
    echo -e "${YELLOW}  Node.js 20 LTS installieren...${NC}"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi

echo -e "${GREEN}  Node $(node -v) | Python $(python3 --version | cut -d' ' -f2)${NC}"

# ── 2. User und Verzeichnis ─────────────────────────────────────────

echo -e "${YELLOW}[2/7] Benutzer und Verzeichnis erstellen...${NC}"
if ! id -u ems &>/dev/null; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /bin/false ems
fi
mkdir -p "$INSTALL_DIR"

# ── 3. Release herunterladen ────────────────────────────────────────

echo -e "${YELLOW}[3/7] Wald EMS herunterladen...${NC}"

# Versuche GitHub Release, sonst clone
RELEASE_URL="https://github.com/${REPO}/releases/latest/download/wald-ems.tar.gz"
if curl -fsSL -o /tmp/wald-ems.tar.gz "$RELEASE_URL" 2>/dev/null; then
    tar -xzf /tmp/wald-ems.tar.gz -C "$INSTALL_DIR"
    rm /tmp/wald-ems.tar.gz
else
    echo -e "${YELLOW}  Kein Release gefunden — klone Repository...${NC}"
    if [ -d "$INSTALL_DIR/.git" ]; then
        cd "$INSTALL_DIR" && git pull
    else
        git clone --depth 1 "https://github.com/${REPO}.git" /tmp/wald-ems-src
        cp -R /tmp/wald-ems-src/* "$INSTALL_DIR/"
        rm -rf /tmp/wald-ems-src
    fi

    # Dashboard bauen
    echo -e "${YELLOW}  Dashboard bauen (npm install + build)...${NC}"
    cd "$INSTALL_DIR"
    npm install --production=false
    npm run build

    # Standalone kopieren
    mkdir -p "$INSTALL_DIR/dashboard/.next"
    cp -R "$INSTALL_DIR/.next/standalone/"* "$INSTALL_DIR/dashboard/"
    cp -R "$INSTALL_DIR/.next/static" "$INSTALL_DIR/dashboard/.next/static"
    [ -d "$INSTALL_DIR/public" ] && cp -R "$INSTALL_DIR/public" "$INSTALL_DIR/dashboard/public"
fi

# ── 4. Python venv ──────────────────────────────────────────────────

echo -e "${YELLOW}[4/7] Python-Umgebung einrichten...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/ems-client/requirements.txt"

# ── 5. Konfiguration ────────────────────────────────────────────────

echo -e "${YELLOW}[5/7] Konfiguration...${NC}"
if [ ! -f "$INSTALL_DIR/wald-ems.yaml" ]; then
    cp "$INSTALL_DIR/wald-ems.yaml.example" "$INSTALL_DIR/wald-ems.yaml"
    echo -e "${YELLOW}  WICHTIG: Bitte $INSTALL_DIR/wald-ems.yaml anpassen!${NC}"
fi

# ── 6. Systemd Services ─────────────────────────────────────────────

echo -e "${YELLOW}[6/7] Systemd Services installieren...${NC}"
cp "$INSTALL_DIR/scripts/wald-ems.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/wald-ems-client.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable wald-ems wald-ems-client

# ── 7. Berechtigungen und Start ─────────────────────────────────────

echo -e "${YELLOW}[7/7] Berechtigungen setzen und starten...${NC}"
chown -R ems:ems "$INSTALL_DIR"

systemctl start wald-ems-client
systemctl start wald-ems

# ── Fertig ───────────────────────────────────────────────────────────

LOCAL_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗"
echo -e "║  Wald EMS erfolgreich installiert!            ║"
echo -e "╠══════════════════════════════════════════════╣"
echo -e "║                                              ║"
echo -e "║  Dashboard: http://${LOCAL_IP}:3000           ║"
echo -e "║  Config:    ${INSTALL_DIR}/wald-ems.yaml     ║"
echo -e "║                                              ║"
echo -e "║  Services:                                   ║"
echo -e "║    systemctl status wald-ems                 ║"
echo -e "║    systemctl status wald-ems-client          ║"
echo -e "║                                              ║"
echo -e "║  Logs:                                       ║"
echo -e "║    journalctl -u wald-ems-client -f          ║"
echo -e "║                                              ║"
echo -e "╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Naechster Schritt: wald-ems.yaml bearbeiten und Services neu starten:${NC}"
echo -e "  sudo nano ${INSTALL_DIR}/wald-ems.yaml"
echo -e "  sudo systemctl restart wald-ems-client"

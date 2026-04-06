#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# Wald EMS — Update-Skript fuer Raspberry Pi
# Nutzung: sudo /opt/ems/scripts/update.sh
# ──────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_DIR="/opt/ems"
REPO="benjiwald/wald-ems"

echo -e "${GREEN}Wald EMS Update${NC}"
echo "═══════════════════════════"

# Root check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Bitte als root: sudo $0${NC}"
    exit 1
fi

# Pruefen ob Installation existiert
if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}Wald EMS nicht installiert. Bitte zuerst installieren:${NC}"
    echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/scripts/install.sh | sudo bash"
    exit 1
fi

cd "$INSTALL_DIR"

# ── 1. Git Pull ─────────────────────────────────────────────────────

echo -e "${YELLOW}[1/4] Code aktualisieren...${NC}"
if [ -d ".git" ]; then
    # Safe: fetch + reset statt pull (vermeidet merge-Konflikte)
    git fetch origin main
    OLD_HASH=$(git rev-parse HEAD)
    git reset --hard origin/main
    NEW_HASH=$(git rev-parse HEAD)
    if [ "$OLD_HASH" = "$NEW_HASH" ]; then
        echo -e "${GREEN}  Bereits aktuell ($OLD_HASH)${NC}"
    else
        COMMITS=$(git rev-list "$OLD_HASH".."$NEW_HASH" --count 2>/dev/null || echo "?")
        echo -e "${GREEN}  ${COMMITS} neue Commits geladen${NC}"
    fi
else
    echo -e "  Kein Git-Repo vorhanden — klone neu..."
    rm -rf /tmp/wald-ems-update
    git clone --depth 1 -b main "https://github.com/${REPO}.git" /tmp/wald-ems-update
    # Config und DB nicht ueberschreiben
    rsync -a --exclude='wald-ems.yaml' --exclude='*.db' --exclude='*.db-wal' \
        --exclude='*.db-shm' --exclude='venv/' --exclude='node_modules/' \
        /tmp/wald-ems-update/ "$INSTALL_DIR/"
    rm -rf /tmp/wald-ems-update
fi

# ── 2. Dashboard neu bauen ──────────────────────────────────────────

echo -e "${YELLOW}[2/4] Dashboard neu bauen...${NC}"
cd "$INSTALL_DIR"

# build-essential sicherstellen (fuer better-sqlite3 native module)
if ! dpkg -s build-essential &>/dev/null; then
    echo -e "  build-essential installieren..."
    apt-get install -y -qq build-essential python3
fi

echo -e "  npm install..."
npm install 2>&1 || { echo -e "${RED}npm install fehlgeschlagen!${NC}"; exit 1; }

echo -e "  next build..."
npm run build 2>&1 || { echo -e "${RED}next build fehlgeschlagen!${NC}"; exit 1; }

# Standalone aktualisieren
rm -rf "$INSTALL_DIR/dashboard"
mkdir -p "$INSTALL_DIR/dashboard/.next"
cp -a "$INSTALL_DIR/.next/standalone/." "$INSTALL_DIR/dashboard/"
cp -a "$INSTALL_DIR/.next/static" "$INSTALL_DIR/dashboard/.next/static"
[ -d "$INSTALL_DIR/public" ] && cp -a "$INSTALL_DIR/public" "$INSTALL_DIR/dashboard/public"
echo -e "${GREEN}  Dashboard gebaut${NC}"

# ── 3. Python Dependencies ──────────────────────────────────────────

echo -e "${YELLOW}[3/4] Python-Dependencies aktualisieren...${NC}"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/ems-client/requirements.txt"
echo -e "${GREEN}  Dependencies aktuell${NC}"

# ── 4. Services neu starten ─────────────────────────────────────────

echo -e "${YELLOW}[4/4] Services neu starten...${NC}"

# Neue systemd-Files kopieren (falls geaendert)
cp "$INSTALL_DIR/scripts/wald-ems.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/wald-ems-client.service" /etc/systemd/system/
systemctl daemon-reload

# Berechtigungen VOR Restart setzen
chown -R ems:ems "$INSTALL_DIR"

systemctl restart wald-ems-client
systemctl restart wald-ems

sleep 2
CLIENT_STATUS=$(systemctl is-active wald-ems-client 2>/dev/null || echo "failed")
DASH_STATUS=$(systemctl is-active wald-ems 2>/dev/null || echo "failed")

echo ""
echo -e "${GREEN}Update abgeschlossen!${NC}"
echo "  Commit:     $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "  Client:     ${CLIENT_STATUS}"
echo "  Dashboard:  ${DASH_STATUS}"
echo "  URL:        http://$(hostname -I | awk '{print $1}'):7777"

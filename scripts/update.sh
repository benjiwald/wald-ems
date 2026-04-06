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
RELEASE_URL="https://github.com/${REPO}/releases/latest/download/wald-ems.tar.gz"

echo -e "${GREEN}Wald EMS Update${NC}"
echo "═══════════════════════════"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Bitte als root: sudo $0${NC}"
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}Wald EMS nicht installiert. Bitte zuerst:${NC}"
    echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/scripts/install.sh | sudo bash"
    exit 1
fi

cd "$INSTALL_DIR"

# Git safe.directory (noetig wenn root auf Repo von ems-User zugreift)
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

OLD_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# ── 1. Pre-built Release herunterladen ──────────────────────────────

echo -e "${YELLOW}[1/3] Neues Release herunterladen...${NC}"

# Config und DB sichern
BACKUP_YAML=""
BACKUP_DB=""
if [ -f "$INSTALL_DIR/wald-ems.yaml" ]; then
    cp "$INSTALL_DIR/wald-ems.yaml" /tmp/wald-ems-yaml-backup
    BACKUP_YAML="1"
fi
if [ -f "$INSTALL_DIR/wald-ems.db" ]; then
    cp "$INSTALL_DIR/wald-ems.db" /tmp/wald-ems-db-backup
    BACKUP_DB="1"
fi
# venv nicht ueberschreiben — merken ob es existiert
HAD_VENV=""
if [ -d "$INSTALL_DIR/venv" ]; then
    HAD_VENV="1"
fi

if curl -fsSL -o /tmp/wald-ems.tar.gz "$RELEASE_URL" 2>/dev/null; then
    echo -e "  Release heruntergeladen — entpacke..."
    tar -xzf /tmp/wald-ems.tar.gz -C "$INSTALL_DIR"
    rm /tmp/wald-ems.tar.gz

    # Auf ARM: better-sqlite3 neu kompilieren (Release ist x86_64)
    ARCH=$(uname -m)
    if [[ "$ARCH" != "x86_64" ]]; then
        echo -e "  Native Module fuer ${ARCH} kompilieren..."
        cd "$INSTALL_DIR/dashboard"
        rm -rf node_modules/better-sqlite3 node_modules/.better-sqlite3*
        npm install better-sqlite3 --no-save 2>&1 | tail -5
        echo -e "${GREEN}  better-sqlite3 fuer ${ARCH} kompiliert${NC}"
    fi

    echo -e "${GREEN}  Release installiert${NC}"
else
    echo -e "${YELLOW}  Kein Release — Fallback auf git pull...${NC}"
    if [ -d ".git" ]; then
        git fetch origin main
        git reset --hard origin/main
    else
        echo -e "${RED}Kein Git-Repo und kein Release verfuegbar!${NC}"
        exit 1
    fi

    # Lokal bauen (Fallback)
    echo -e "  Baue lokal (kann 5-10 Min dauern)..."
    npm install 2>&1 | tail -5
    npm run build 2>&1 | tail -5

    rm -rf "$INSTALL_DIR/dashboard"
    mkdir -p "$INSTALL_DIR/dashboard/.next"
    cp -a "$INSTALL_DIR/.next/standalone/." "$INSTALL_DIR/dashboard/"
    cp -a "$INSTALL_DIR/.next/static" "$INSTALL_DIR/dashboard/.next/static"
    [ -d "$INSTALL_DIR/public" ] && cp -a "$INSTALL_DIR/public" "$INSTALL_DIR/dashboard/public"
fi

# Config und DB wiederherstellen
if [ "$BACKUP_YAML" = "1" ]; then
    cp /tmp/wald-ems-yaml-backup "$INSTALL_DIR/wald-ems.yaml"
    rm /tmp/wald-ems-yaml-backup
    echo -e "  wald-ems.yaml beibehalten"
fi
if [ "$BACKUP_DB" = "1" ]; then
    cp /tmp/wald-ems-db-backup "$INSTALL_DIR/wald-ems.db"
    rm /tmp/wald-ems-db-backup
    echo -e "  wald-ems.db beibehalten"
fi

# Git-Repo aktualisieren (fuer Update-Check im Dashboard)
if [ -d "$INSTALL_DIR/.git" ]; then
    git fetch origin main 2>/dev/null && git reset --hard origin/main 2>/dev/null || true
fi

# ── 2. Python Dependencies ──────────────────────────────────────────

echo -e "${YELLOW}[2/3] Python-Dependencies aktualisieren...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo -e "  Python venv neu erstellen..."
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/ems-client/requirements.txt"
echo -e "${GREEN}  Dependencies aktuell${NC}"

# ── 3. Services neu starten ─────────────────────────────────────────

echo -e "${YELLOW}[3/3] Services neu starten...${NC}"

cp "$INSTALL_DIR/scripts/wald-ems.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/wald-ems-client.service" /etc/systemd/system/
systemctl daemon-reload

chown -R ems:ems "$INSTALL_DIR"

systemctl restart wald-ems-client
systemctl restart wald-ems

sleep 2
CLIENT_STATUS=$(systemctl is-active wald-ems-client 2>/dev/null || echo "failed")
DASH_STATUS=$(systemctl is-active wald-ems 2>/dev/null || echo "failed")
NEW_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

echo ""
echo -e "${GREEN}Update abgeschlossen!${NC}"
echo "  Version:    ${OLD_HASH} → ${NEW_HASH}"
echo "  Client:     ${CLIENT_STATUS}"
echo "  Dashboard:  ${DASH_STATUS}"
echo "  URL:        http://$(hostname -I | awk '{print $1}'):7777"

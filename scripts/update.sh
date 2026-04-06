#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# Wald EMS — Update-Skript fuer Raspberry Pi
# Nutzung: sudo /opt/ems/scripts/update.sh
# ──────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="/opt/ems"

echo -e "${GREEN}Wald EMS Update${NC}"
echo "═══════════════════════════"

# Root check
if [ "$EUID" -ne 0 ]; then
    echo "Bitte als root: sudo $0"
    exit 1
fi

cd "$INSTALL_DIR"

# ── 1. Git Pull ─────────────────────────────────────────────────────

echo -e "${YELLOW}[1/4] Code aktualisieren...${NC}"
if [ -d ".git" ]; then
    sudo -u ems git pull origin main
else
    echo "  Kein Git-Repo — lade neu herunter..."
    git clone --depth 1 https://github.com/benjiwald/wald-ems.git /tmp/wald-ems-update
    rsync -a --exclude=wald-ems.yaml --exclude=wald-ems.db /tmp/wald-ems-update/ "$INSTALL_DIR/"
    rm -rf /tmp/wald-ems-update
fi

# ── 2. Dashboard neu bauen ──────────────────────────────────────────

echo -e "${YELLOW}[2/4] Dashboard neu bauen...${NC}"
npm install --production=false
npm run build

# Standalone aktualisieren
rm -rf "$INSTALL_DIR/dashboard"
mkdir -p "$INSTALL_DIR/dashboard/.next"
cp -R "$INSTALL_DIR/.next/standalone/"* "$INSTALL_DIR/dashboard/"
cp -R "$INSTALL_DIR/.next/static" "$INSTALL_DIR/dashboard/.next/static"
[ -d "$INSTALL_DIR/public" ] && cp -R "$INSTALL_DIR/public" "$INSTALL_DIR/dashboard/public"

# ── 3. Python Dependencies ──────────────────────────────────────────

echo -e "${YELLOW}[3/4] Python-Dependencies aktualisieren...${NC}"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/ems-client/requirements.txt"

# ── 4. Services neu starten ─────────────────────────────────────────

echo -e "${YELLOW}[4/4] Services neu starten...${NC}"

# Neue systemd-Files kopieren (falls geaendert)
cp "$INSTALL_DIR/scripts/wald-ems.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/wald-ems-client.service" /etc/systemd/system/
systemctl daemon-reload

systemctl restart wald-ems-client
systemctl restart wald-ems

# Berechtigungen
chown -R ems:ems "$INSTALL_DIR"

echo ""
echo -e "${GREEN}Update abgeschlossen!${NC}"
echo "  Dashboard:   http://$(hostname -I | awk '{print $1}'):3000"
echo "  Client-Logs: journalctl -u wald-ems-client -f"

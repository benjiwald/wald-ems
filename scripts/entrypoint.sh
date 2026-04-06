#!/bin/bash
set -e

# Config aus Example kopieren wenn noch keine existiert
if [ ! -f /data/wald-ems.yaml ]; then
    cp /opt/ems/wald-ems.yaml.example /data/wald-ems.yaml
    echo "wald-ems.yaml erstellt in /data/ — bitte anpassen!"
fi

# DB-Pfad auf /data/ setzen falls nicht konfiguriert
export WALD_EMS_CONFIG=/data/wald-ems.yaml

# Python EMS Client im Hintergrund starten
echo "Starte EMS Client..."
python3 /opt/ems/ems-client/main.py &
CLIENT_PID=$!

# Next.js Dashboard starten
echo "Starte Dashboard auf Port ${PORT:-7777}..."
node /opt/ems/dashboard/server.js &
DASH_PID=$!

echo "Wald EMS laeuft (Client PID=$CLIENT_PID, Dashboard PID=$DASH_PID)"

# Auf Signal warten — beide Prozesse sauber beenden
trap "kill $CLIENT_PID $DASH_PID 2>/dev/null; exit 0" SIGTERM SIGINT

wait -n
echo "Ein Prozess beendet — stoppe alles"
kill $CLIENT_PID $DASH_PID 2>/dev/null
exit 1

#!/usr/bin/env bash
# Startet den Tab-Recorder-Server im Hintergrund (überlebt Terminal-Exit).
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8787}"
URL="http://localhost:${PORT}/"

# Schon laufend? → nur Edge öffnen
if ss -tln 2>/dev/null | grep -q ":${PORT} "; then
  echo "Server läuft bereits auf ${URL}"
else
  echo "Starte Server auf ${URL}"
  setsid python3 -m http.server "${PORT}" --bind 127.0.0.1 </dev/null >/tmp/webvideo.log 2>&1 &
  disown
  sleep 0.8
fi

# Edge öffnen
setsid microsoft-edge-stable --new-window "${URL}" </dev/null >/dev/null 2>&1 &
disown

echo "Edge-Fenster öffnet sich gleich. Stoppen: pkill -f 'http.server ${PORT}'"

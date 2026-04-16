#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/transcriptor"
SERVICE_DIR="$HOME/.config/systemd/user"
NEED_RELOGIN=false

echo "=== Blitztext Linux Installer ==="
echo "Projektverzeichnis: $PROJECT_DIR"

# 1. System-Abhängigkeiten prüfen
echo ""
echo "--- Prüfe Abhängigkeiten ---"
for tool in xdotool notify-send arecord; do
    if ! command -v "$tool" &>/dev/null; then
        echo "WARNUNG: $tool fehlt. Installieren mit: sudo pacman -S $tool"
    else
        echo "  ✓ $tool"
    fi
done

# Benutzer zur input-Gruppe hinzufügen (für evdev ohne root)
if ! groups | grep -q input; then
    echo ""
    echo "Füge $USER zur input-Gruppe hinzu..."
    sudo usermod -aG input "$USER"
    echo ""
    echo "╔════════════════════════════════════════════════════╗"
    echo "║  WICHTIG: Gruppe 'input' wurde hinzugefügt.       ║"
    echo "║  Du musst dich ABMELDEN und NEU ANMELDEN bevor    ║"
    echo "║  der Service auf /dev/input/eventX zugreifen kann.║"
    echo "║  Nach dem Neuanmelden:                            ║"
    echo "║    systemctl --user start blitztext.service       ║"
    echo "╚════════════════════════════════════════════════════╝"
    NEED_RELOGIN=true
else
    echo "  ✓ input-Gruppe bereits vorhanden"
fi

# 2. Python venv + Deps
echo ""
echo "--- Installiere Python-Abhängigkeiten ---"
if [ ! -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    python -m venv "$PROJECT_DIR/.venv"
fi
source "$PROJECT_DIR/.venv/bin/activate"
pip install -q -e "$PROJECT_DIR"
echo "  ✓ Python-Pakete installiert"

# 3. Config-Verzeichnis + Standard-Config anlegen
echo ""
echo "--- Konfiguration ---"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    echo "Erstelle Standard-Config in $CONFIG_DIR/config.json"
    BLITZTEXT_CONFIG="$CONFIG_DIR/config.json" python -c "
from app.config import load_config
load_config()
print('  ✓ Standard-Config erstellt')
"
else
    echo "  ✓ Config bereits vorhanden: $CONFIG_DIR/config.json"
fi

# API-Key in env-Datei schreiben
if [ -n "$OPENAI_API_KEY" ]; then
    echo "OPENAI_API_KEY=$OPENAI_API_KEY" > "$CONFIG_DIR/env"
    echo "  ✓ OPENAI_API_KEY gespeichert"
else
    echo "  HINWEIS: Setze OPENAI_API_KEY in $CONFIG_DIR/env"
    echo "  Beispiel: echo 'OPENAI_API_KEY=sk-...' > $CONFIG_DIR/env"
fi

# 4. systemd User-Service installieren
echo ""
echo "--- systemd Service ---"
mkdir -p "$SERVICE_DIR"
sed "s|%h|$HOME|g; s|/mnt/data/Projects/blitztext-linux|$PROJECT_DIR|g" \
    "$PROJECT_DIR/blitztext.service" > "$SERVICE_DIR/blitztext.service"
systemctl --user daemon-reload
systemctl --user enable blitztext.service
echo "  ✓ Service aktiviert"

if [ "$NEED_RELOGIN" != "true" ]; then
    systemctl --user start blitztext.service
    sleep 1
    if systemctl --user is-active --quiet blitztext.service; then
        echo "  ✓ Service läuft"
    else
        echo "  WARNUNG: Service konnte nicht gestartet werden"
        echo "  Logs: journalctl --user -u blitztext.service -n 20"
    fi
else
    echo "  Service aktiviert, aber NICHT gestartet — erst neu anmelden!"
fi

# 5. Tray-App installieren
echo ""
echo "--- Tray-App ---"
VENV_BIN="$PROJECT_DIR/.venv/bin"
sed "s|VENV_PATH|$VENV_BIN|g; s|PROJECT_DIR|$PROJECT_DIR|g" \
    "$PROJECT_DIR/blitztext-tray.service" > "$SERVICE_DIR/blitztext-tray.service"
systemctl --user daemon-reload
systemctl --user enable blitztext-tray.service
echo "  ✓ Tray-Service aktiviert"

if [ "$NEED_RELOGIN" != "true" ]; then
    # Tray benötigt eine laufende graphical session
    if [ -n "$WAYLAND_DISPLAY" ] || [ -n "$DISPLAY" ]; then
        systemctl --user start blitztext-tray.service
        sleep 1
        if systemctl --user is-active --quiet blitztext-tray.service; then
            echo "  ✓ Tray-App läuft"
        else
            echo "  HINWEIS: Tray-Service konnte nicht gestartet werden"
            echo "  Manuell starten: $VENV_BIN/blitztext-tray &"
        fi
    else
        echo "  HINWEIS: Kein DISPLAY/WAYLAND_DISPLAY — Tray startet beim nächsten Login"
    fi
fi

echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║         Installation abgeschlossen             ║"
echo "╚════════════════════════════════════════════════╝"
echo ""
echo "Nächste Schritte:"
echo "  Daemon:  systemctl --user status blitztext.service"
echo "  Tray:    systemctl --user status blitztext-tray.service"
echo "  Health:  curl http://localhost:8765/api/health"
echo "  Config:  Rechtsklick auf Tray-Icon → Einstellungen"

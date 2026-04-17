#!/usr/bin/env bash
set -e

# ── Farben ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}OK${NC}  $1"; }
warn() { echo -e "  ${YELLOW}!!${NC}  $1"; }
fail() { echo -e "  ${RED}XX${NC}  $1"; }
info() { echo -e "  ${CYAN}--${NC}  $1"; }
header() { echo -e "\n${BOLD}=== $1 ===${NC}"; }

# ── Variablen ─────────────────────────────────────────────────────────────────
INSTALL_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
SERVICE_DIR="$HOME/.config/systemd/user"
CONFIG_DIR="$HOME/.config/transcriptor"
UDEV_RULE="/etc/udev/rules.d/99-stt-trans-input.rules"
NEED_RELOGIN=false
MIN_PYTHON="3.12"

echo -e "${BOLD}"
echo "  ____  _ _ _       _____         _   "
echo " | __ )| (_) |_ ___  |_   _|____  _| |_ "
echo " |  _ \\| | | __|_  /   | |/ _ \\ \\/ / __|"
echo " | |_) | | | |_ / /    | |  __/>  <| |_ "
echo " |____/|_|_|\\__/___|   |_|\\___/_/\\_\\\\__|"
echo -e "${NC}"
echo -e "Installationsverzeichnis: ${CYAN}${INSTALL_DIR}${NC}"

# ── 1. Paketmanager erkennen ──────────────────────────────────────────────────
header "Paketmanager erkennen"

PKG_MGR=""
PKG_INSTALL=""

if command -v pacman &>/dev/null; then
    PKG_MGR="pacman"
    PKG_INSTALL="sudo pacman -S --noconfirm --needed"
    ok "Arch Linux (pacman)"
elif command -v apt &>/dev/null; then
    PKG_MGR="apt"
    PKG_INSTALL="sudo apt install -y"
    ok "Debian/Ubuntu (apt)"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
    PKG_INSTALL="sudo dnf install -y"
    ok "Fedora (dnf)"
else
    fail "Kein unterstuetzter Paketmanager gefunden (pacman/apt/dnf)"
    exit 1
fi

# ── 2. Python 3.12+ pruefen ──────────────────────────────────────────────────
header "Python pruefen"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" &>/dev/null; then
        PY_VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    fail "Python >= ${MIN_PYTHON} nicht gefunden."
    info "Installieren mit:"
    case "$PKG_MGR" in
        pacman) info "  sudo pacman -S python" ;;
        apt)    info "  sudo apt install python3.12 python3.12-venv" ;;
        dnf)    info "  sudo dnf install python3.12" ;;
    esac
    exit 1
fi

ok "$(command -v "$PYTHON_BIN") ($("$PYTHON_BIN" --version 2>&1))"

# pip / venv Modul pruefen
if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
    warn "pip fehlt -- versuche zu installieren"
    case "$PKG_MGR" in
        pacman) $PKG_INSTALL python-pip ;;
        apt)    $PKG_INSTALL python3-pip ;;
        dnf)    $PKG_INSTALL python3-pip ;;
    esac
fi

if ! "$PYTHON_BIN" -c "import venv" &>/dev/null; then
    warn "venv Modul fehlt -- versuche zu installieren"
    case "$PKG_MGR" in
        pacman) ok "venv ist bei Arch in python enthalten" ;;
        apt)    $PKG_INSTALL "python${PY_VER}-venv" ;;
        dnf)    ok "venv ist bei Fedora in python3 enthalten" ;;
    esac
fi

# ── 3. System-Tools pruefen ──────────────────────────────────────────────────
header "System-Tools pruefen"

check_tool() {
    local tool="$1"
    local pkg_pacman="$2"
    local pkg_apt="$3"
    local pkg_dnf="$4"
    local required="$5"  # "required" or "optional"

    if command -v "$tool" &>/dev/null; then
        ok "$tool"
        return 0
    fi

    if [ "$required" = "required" ]; then
        fail "$tool fehlt (erforderlich)"
    else
        warn "$tool fehlt (optional)"
    fi

    case "$PKG_MGR" in
        pacman) info "  $PKG_INSTALL $pkg_pacman" ;;
        apt)    info "  $PKG_INSTALL $pkg_apt" ;;
        dnf)    info "  $PKG_INSTALL $pkg_dnf" ;;
    esac
    return 1
}

MISSING_REQUIRED=false

check_tool "xdotool"    "xdotool"       "xdotool"       "xdotool"       "optional" || true
check_tool "wtype"      "wtype"         "wtype"         "wtype"         "optional" || true
check_tool "ydotool"    "ydotool"       "ydotool"       "ydotool"       "optional" || true
check_tool "notify-send" "libnotify"    "libnotify-bin" "libnotify"     "optional" || true
check_tool "wl-copy"    "wl-clipboard"  "wl-clipboard"  "wl-clipboard"  "optional" || true

# Mindestens ein Inject-Tool muss vorhanden sein
if ! command -v wtype &>/dev/null && \
   ! command -v xdotool &>/dev/null && \
   ! command -v ydotool &>/dev/null && \
   ! command -v wl-copy &>/dev/null; then
    fail "Mindestens eines der Inject-Tools wird benoetigt: wtype, xdotool, ydotool, wl-copy"
    MISSING_REQUIRED=true
fi

if [ "$MISSING_REQUIRED" = "true" ]; then
    fail "Erforderliche System-Tools fehlen. Bitte zuerst installieren."
    exit 1
fi

# ── 4. Virtuelle Umgebung + pip install ───────────────────────────────────────
header "Python-Umgebung einrichten"

if [ ! -f "$INSTALL_DIR/.venv/bin/activate" ]; then
    info "Erstelle .venv mit $PYTHON_BIN ..."
    "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
    ok "Virtuelle Umgebung erstellt"
else
    ok "Virtuelle Umgebung existiert bereits"
fi

source "$INSTALL_DIR/.venv/bin/activate"

info "Installiere Abhaengigkeiten (pip install -e .) ..."
pip install -q --upgrade pip
pip install -q -e "$INSTALL_DIR"
ok "Projekt installiert (stt-trans)"

# ── 5. Optional: faster-whisper (local) ──────────────────────────────────────
header "Optionale Abhaengigkeiten"

echo ""
echo -e "  Moechtest du ${BOLD}faster-whisper${NC} fuer lokale Transkription installieren?"
echo -e "  (benoetigt ca. 1-2 GB Speicher fuer Modelle)"
read -r -p "  [j/N] " INSTALL_LOCAL

if [[ "$INSTALL_LOCAL" =~ ^[jJyY]$ ]]; then
    info "Installiere faster-whisper ..."
    pip install -q -e "$INSTALL_DIR[local]"
    ok "faster-whisper installiert"
else
    info "Uebersprungen. Spaeter mit: pip install -e '.[local]'"
fi

# ── 6. .env Datei ────────────────────────────────────────────────────────────
header "Konfiguration (.env)"

if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    warn ".env aus .env.example erstellt"
    echo ""
    echo -e "  ${YELLOW}Bitte trage deine API-Keys ein:${NC}"
    echo -e "    ${CYAN}$INSTALL_DIR/.env${NC}"
    echo ""
    echo "  Mindestens OPENAI_API_KEY wird fuer den Online-Modus benoetigt."
    echo ""
else
    ok ".env existiert bereits"
fi

# Config-Verzeichnis + Standard-Config
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    BLITZTEXT_CONFIG="$CONFIG_DIR/config.json" "$INSTALL_DIR/.venv/bin/python" -c "
from app.config import load_config
load_config()
" 2>/dev/null
    ok "Standard-Config erstellt: $CONFIG_DIR/config.json"
else
    ok "Config vorhanden: $CONFIG_DIR/config.json"
fi

# ── 7. udev-Regel fuer /dev/input/event* ─────────────────────────────────────
header "evdev-Zugriff (udev + input-Gruppe)"

# Benutzer zur input-Gruppe hinzufuegen
if ! groups | grep -qw input; then
    info "Fuege $USER zur input-Gruppe hinzu ..."
    sudo usermod -aG input "$USER"
    warn "Gruppe 'input' hinzugefuegt -- Abmeldung/Neuanmeldung noetig!"
    NEED_RELOGIN=true
else
    ok "$USER ist bereits in der input-Gruppe"
fi

# udev-Regel schreiben
if [ ! -f "$UDEV_RULE" ]; then
    info "Erstelle udev-Regel: $UDEV_RULE"
    sudo tee "$UDEV_RULE" > /dev/null <<'UDEV'
# stt-trans: Grant input group access to event devices for evdev key capture
KERNEL=="event*", SUBSYSTEM=="input", GROUP="input", MODE="0660"
UDEV
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=input
    ok "udev-Regel installiert und aktiviert"
else
    ok "udev-Regel existiert bereits"
fi

# ── 8. systemd User-Services ─────────────────────────────────────────────────
header "systemd User-Services installieren"

mkdir -p "$SERVICE_DIR"

# --- stt-trans.service ---
cat > "$SERVICE_DIR/stt-trans.service" <<EOF
[Unit]
Description=stt-trans Diktierdienst
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=${INSTALL_DIR}/.env

[Install]
WantedBy=default.target
EOF
ok "stt-trans.service geschrieben"

# --- stt-trans-tray.service ---
cat > "$SERVICE_DIR/stt-trans-tray.service" <<EOF
[Unit]
Description=stt-trans System-Tray
After=stt-trans.service graphical-session.target
Wants=stt-trans.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/stt-trans-tray
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/%U

[Install]
WantedBy=graphical-session.target
EOF
ok "stt-trans-tray.service geschrieben"

# daemon-reload
systemctl --user daemon-reload
ok "systemd daemon-reload"

# enable
systemctl --user enable stt-trans.service
systemctl --user enable stt-trans-tray.service
ok "Services aktiviert (enable)"

# ── 9. Services starten ──────────────────────────────────────────────────────
header "Services starten"

if [ "$NEED_RELOGIN" = "true" ]; then
    warn "Neuanmeldung erforderlich (input-Gruppe)"
    info "Services sind aktiviert, starten aber erst nach dem naechsten Login."
    info "Danach manuell starten mit:"
    info "  systemctl --user start stt-trans.service"
    info "  systemctl --user start stt-trans-tray.service"
else
    # Daemon starten
    systemctl --user restart stt-trans.service
    sleep 1
    if systemctl --user is-active --quiet stt-trans.service; then
        ok "stt-trans.service laeuft"
    else
        fail "stt-trans.service konnte nicht gestartet werden"
        info "Logs: journalctl --user -u stt-trans.service -n 30"
    fi

    # Tray starten (nur wenn Display vorhanden)
    if [ -n "$WAYLAND_DISPLAY" ] || [ -n "$DISPLAY" ]; then
        systemctl --user restart stt-trans-tray.service
        sleep 1
        if systemctl --user is-active --quiet stt-trans-tray.service; then
            ok "stt-trans-tray.service laeuft"
        else
            warn "stt-trans-tray.service konnte nicht gestartet werden"
            info "Logs: journalctl --user -u stt-trans-tray.service -n 30"
        fi
    else
        warn "Kein DISPLAY/WAYLAND_DISPLAY -- Tray startet beim naechsten Login"
    fi
fi

# ── 10. Abschluss ────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ================================================"
echo "     Installation abgeschlossen"
echo "  ================================================"
echo -e "${NC}"

echo -e "  ${BOLD}Status pruefen:${NC}"
echo "    systemctl --user status stt-trans.service"
echo "    systemctl --user status stt-trans-tray.service"
echo ""
echo -e "  ${BOLD}Health-Check:${NC}"
echo "    curl http://localhost:8765/api/health"
echo ""
echo -e "  ${BOLD}Logs:${NC}"
echo "    journalctl --user -u stt-trans.service -f"
echo ""
echo -e "  ${BOLD}Config bearbeiten:${NC}"
echo "    $INSTALL_DIR/.env          (API-Keys)"
echo "    $CONFIG_DIR/config.json    (Einstellungen)"
echo "    Rechtsklick auf Tray-Icon  (GUI)"
echo ""

if [ "$NEED_RELOGIN" = "true" ]; then
    echo -e "  ${YELLOW}${BOLD}WICHTIG: Bitte abmelden und neu anmelden,${NC}"
    echo -e "  ${YELLOW}${BOLD}damit die input-Gruppe aktiv wird.${NC}"
    echo ""
fi

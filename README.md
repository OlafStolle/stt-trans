# Blitztext Linux

Systemweiter Diktierdienst für Linux. Taste halten → sprechen → loslassen → Text erscheint im aktiven Fenster.

Basiert auf FastAPI + evdev + OpenAI Whisper + xdotool.

## Features

- **4 Modi** via konfigurierbare Tasten:
  - **Normal** (KEY_F13): Wort-für-Wort Transkription
  - **Plus** (KEY_F14): Sprachtext → schriftlich formuliert
  - **Rage** (KEY_F15): Wütender Text → höfliche Ausgabe
  - **Emoji** (KEY_F16): Text + passende Emojis
- **Push-to-Talk** (halten) oder **Toggle** (drücken/drücken)
- **Systemweit**: funktioniert in jeder App (Slack, Browser, Terminal)
- **systemd User-Service**: startet automatisch beim Login
- **Config REST API**: alle Einstellungen per curl konfigurierbar

## Voraussetzungen

- Linux (X11 oder Wayland mit xdotool-Support)
- Python 3.12+
- `xdotool`, `notify-send`, `arecord` (alsa-utils)
- OpenAI API Key

```bash
sudo pacman -S xdotool libnotify alsa-utils  # Arch/CachyOS
# oder: sudo apt install xdotool libnotify-bin alsa-utils  # Debian/Ubuntu
```

## Installation

```bash
git clone <repo> blitztext-linux
cd blitztext-linux
OPENAI_API_KEY=sk-... bash install.sh
```

## Konfiguration

Nach der Installation Tastatur-Device ermitteln und setzen:

```bash
# Verfügbare Input-Geräte
ls /dev/input/event*

# Gerät konfigurieren
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"input_device": "/dev/input/event3"}'

# Status prüfen
curl http://localhost:8765/api/health
```

### Tastenkürzel anpassen

```bash
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "modes": {
      "normal": {"key_code": 183, "key_name": "KEY_F13"},
      "plus":   {"key_code": 184, "key_name": "KEY_F14"},
      "rage":   {"key_code": 185, "key_name": "KEY_F15"},
      "emoji":  {"key_code": 186, "key_name": "KEY_F16", "emoji_count": "mittel"}
    }
  }'
```

### Trigger-Modus

```bash
# Hold (Standard): Taste halten = aufnehmen, loslassen = einfügen
curl -X PATCH http://localhost:8765/api/config \
  -d '{"trigger_mode": "hold"}' -H "Content-Type: application/json"

# Toggle: einmal drücken = Start, nochmal = Stop+Einfügen
curl -X PATCH http://localhost:8765/api/config \
  -d '{"trigger_mode": "toggle"}' -H "Content-Type: application/json"
```

## Service-Verwaltung

```bash
systemctl --user status blitztext.service   # Status
systemctl --user restart blitztext.service  # Neustart
journalctl --user -u blitztext.service -f   # Live-Logs
```

## API-Übersicht

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/health` | GET | Service-Status |
| `/api/config` | GET | Konfiguration lesen |
| `/api/config` | PATCH | Konfiguration ändern |
| `/api/config/reset` | POST | Auf Defaults zurücksetzen |
| `/api/process/{mode}` | POST | Text manuell verarbeiten |

## Konfigurationsdatei

`~/.config/transcriptor/config.json` — wird beim ersten Start automatisch angelegt.

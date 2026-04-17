# stt-trans

Systemweiter Diktierdienst fuer Linux (X11 + Wayland). Taste druecken, sprechen, Text erscheint im aktiven Fenster — in jeder Anwendung.

<!-- Screenshot -->

## Features

- **Push-to-Talk / Toggle** — Taste halten oder einmal druecken zum Aufnehmen, Text wird automatisch eingefuegt
- **4 Diktiermodi**:
  - **Normal** — wortgetreue Transkription
  - **Plus** — gesprochenen Text schriftlich formuliert
  - **Rage** — wuetenden Text in hoefliche Formulierung umwandeln
  - **Emoji** — Text mit passenden Emojis anreichern
- **Live-Transkription** — Echtzeit-Untertitel im Browser mit automatischer Uebersetzung (Mikrofon + Desktop-Audio)
- **Lokale oder Online Transkription** — faster-whisper (lokal, offline) oder OpenAI Whisper API (online)
- **System-Tray App** — Einstellungen, Tastenerkennung, Mikrofon-Auswahl per GUI
- **SayoDevice-kompatibel** — Macro-Pads mit Tap-Firmware, Multi-Key-Combos
- **Systemweit** — funktioniert in jeder App (Browser, Slack, Terminal, IDE)
- **systemd User-Services** — Autostart beim Login

## Voraussetzungen

- Linux mit X11 oder Wayland
- Python 3.12+
- OpenAI API Key (fuer Online-Modus) oder CUDA/CPU fuer lokalen Modus
- Benutzer muss Mitglied der `input`-Gruppe sein (fuer evdev-Zugriff)

### Systempakete

```bash
# Arch / CachyOS
sudo pacman -S libnotify alsa-utils

# Wayland (empfohlen)
sudo pacman -S wl-clipboard ydotool

# X11
sudo pacman -S xdotool xclip
```

```bash
# Debian / Ubuntu
sudo apt install libnotify-bin alsa-utils

# Wayland
sudo apt install wl-clipboard ydotool

# X11
sudo apt install xdotool xclip
```

## Installation

```bash
git clone <repo-url> stt-trans
cd stt-trans
OPENAI_API_KEY=sk-... bash install.sh
```

Das Installationsskript erledigt folgende Schritte:

1. Prueft System-Abhaengigkeiten
2. Fuegt den Benutzer zur `input`-Gruppe hinzu (falls noetig)
3. Erstellt ein Python-venv und installiert alle Pakete
4. Legt die Standard-Konfiguration an (`~/.config/transcriptor/config.json`)
5. Installiert und aktiviert die systemd-Services

Nach der Installation ist ein Neuanmelden erforderlich, falls die `input`-Gruppe neu hinzugefuegt wurde.

## Konfiguration

### Per Tray-App (empfohlen)

Rechtsklick auf das Tray-Icon und "Einstellungen oeffnen" waehlen. Die GUI bietet:

- Eingabegeraet (Tastatur) auswaehlen
- Audiogeraet (Mikrofon) waehlen
- Tasten fuer jeden Modus zuweisen (Taste druecken statt Key-Code eingeben)
- Trigger-Modus (Hold / Toggle) einstellen
- Inject-Methode waehlen (wtype, xdotool, wl-copy+paste, etc.)
- Live-Transkription Tastenkombination festlegen

### Per CLI (curl)

```bash
# Input-Device setzen (evdev-Pfad)
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"input_device": "/dev/input/event3"}'

# Trigger-Modus aendern
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"trigger_mode": "toggle"}'

# Inject-Methode fuer Wayland
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"inject": {"method": "wl-copy+paste", "paste_shortcut": "ctrl+shift+v"}}'

# Tastenkuerzel anpassen
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "modes": {
      "normal": {"key_codes": [183], "key_name": "KEY_F13"},
      "plus":   {"key_codes": [184], "key_name": "KEY_F14"},
      "rage":   {"key_codes": [185], "key_name": "KEY_F15"},
      "emoji":  {"key_codes": [186], "key_name": "KEY_F16"}
    }
  }'

# Aktuelle Konfiguration anzeigen
curl http://localhost:8765/api/config | python -m json.tool
```

### Konfigurationsdatei

Speicherort: `~/.config/transcriptor/config.json`

Wird beim ersten Start automatisch angelegt. Kann alternativ ueber die Umgebungsvariable `BLITZTEXT_CONFIG` auf einen anderen Pfad gesetzt werden.

## Benutzung

### Diktiermodi

| Modus | Standard-Taste | Beschreibung |
|-------|---------------|--------------|
| Normal | KEY_F13 | Wortgetreue Transkription |
| Plus | KEY_F14 | Gesprochener Text wird schriftlich formuliert |
| Rage | KEY_F15 | Wuetender Text wird hoeflich umformuliert |
| Emoji | KEY_F16 | Text wird mit Emojis angereichert |

**Hold-Modus** (Standard): Taste halten = Aufnahme laeuft, Taste loslassen = Text wird eingefuegt. Bei kurzem Tap (<400ms) wechselt die Aufnahme automatisch in den Toggle-Modus.

**Toggle-Modus**: Einmal druecken = Aufnahme starten, nochmal druecken = Aufnahme stoppen und Text einfuegen.

### Live-Transkription

Die Live-Transkription zeigt Echtzeit-Untertitel im Browser mit optionaler Uebersetzung.

1. Tastenkombination fuer Live-Modus in den Einstellungen festlegen
2. Kombination druecken — der Browser oeffnet sich automatisch unter `http://localhost:8765/live`
3. Mikrofon und Desktop-Audio werden parallel transkribiert
4. Zielsprache fuer Uebersetzung im Browser-UI waehlen
5. Nochmal die Kombination druecken oder den Stop-Button im Browser verwenden

### Tray-App

Die Tray-App zeigt den aktuellen Status (idle / recording) und bietet:

- **Einstellungen oeffnen** — Konfigurationsfenster mit allen Optionen
- **Neustart** — startet den Daemon-Service neu
- **Beenden** — beendet die Tray-App

### Service-Verwaltung

```bash
# Daemon-Status
systemctl --user status stt-trans.service

# Tray-Status
systemctl --user status stt-trans-tray.service

# Daemon neustarten
systemctl --user restart stt-trans.service

# Live-Logs
journalctl --user -u stt-trans.service -f
```

## API-Uebersicht

Der Daemon lauscht auf `http://localhost:8765`.

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/health` | GET | Service-Status, Version, konfigurierte Geraete |
| `/api/status` | GET | Aktueller Aufnahmestatus und aktiver Modus |
| `/api/config` | GET | Gesamte Konfiguration auslesen |
| `/api/config` | PATCH | Konfiguration teilweise aktualisieren |
| `/api/config/reset` | POST | Konfiguration auf Standardwerte zuruecksetzen |
| `/api/process/{mode}` | POST | Text manuell durch einen Modus verarbeiten |
| `/live` | GET | Live-Transkription HTML-Seite |
| `/live/stop` | POST | Live-Transkription stoppen |
| `/ws/live/mic` | WebSocket | Mikrofon-Stream fuer Live-Transkription |
| `/ws/live/desktop` | WebSocket | Desktop-Audio-Stream fuer Live-Transkription |

### Beispiele

```bash
# Health-Check
curl http://localhost:8765/api/health

# Text manuell durch "plus"-Modus verarbeiten
curl -X POST http://localhost:8765/api/process/plus \
  -H "Content-Type: application/json" \
  -d '{"text": "also ich wollte sagen dass das echt cool ist"}'

# Aufnahmestatus abfragen
curl http://localhost:8765/api/status
```

## Lokale Transkription (faster-whisper)

Fuer offline-faehige Transkription ohne OpenAI API:

```bash
# faster-whisper installieren
cd stt-trans
source .venv/bin/activate
pip install faster-whisper

# Backend umschalten
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"transcribe_backend": "local", "local_whisper_model": "small"}'
```

Verfuegbare Modellgroessen: `tiny`, `base`, `small`, `medium`. Groessere Modelle liefern bessere Ergebnisse, benoetigen aber mehr RAM und Rechenzeit. Das Modell wird beim ersten Aufruf heruntergeladen und im RAM gehalten.

Mit CUDA-faehiger GPU wird automatisch GPU-Beschleunigung verwendet.

### Inject-Methoden

| Methode | Umgebung | Beschreibung |
|---------|----------|--------------|
| `wl-copy+paste` | Wayland | Clipboard + ydotool-Paste (empfohlen fuer Wayland) |
| `wtype` | Wayland | Direkte Tastatureingabe via wtype |
| `ydotool` | Wayland | Tastatureingabe via ydotool |
| `xdotool` | X11 | Direkte Tastatureingabe via xdotool |
| `xclip+paste` | X11 | Clipboard + xdotool-Paste |

## Technischer Stack

- Python 3.12+, FastAPI, uvicorn
- evdev (globale Tastatur-Events)
- sounddevice + numpy + scipy (Audioaufnahme und Resampling)
- OpenAI API / faster-whisper (Transkription)
- pystray + tkinter (System-Tray GUI)
- WebSocket (Live-Transkription Streaming)
- systemd User-Services (Autostart)

## Lizenz

MIT License — siehe [LICENSE](LICENSE). Freie Nutzung und Kopieren erlaubt.

## Contributing

Issues-first Policy: Bitte zuerst ein Issue erstellen und das Problem oder Feature beschreiben, bevor ein Pull Request eingereicht wird.

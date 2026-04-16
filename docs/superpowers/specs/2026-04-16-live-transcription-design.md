# Design: Live-Transkription mit Übersetzung

**Date:** 2026-04-16
**Project:** blitztext-linux (`/mnt/data/Projects/blitztext-linux`)
**Status:** Approved

---

## Summary

Dual-Stream Live-Transkription: Mikrofon + Desktop-Audio (PipeWire Monitor) laufen parallel. Beim Toggle-Key öffnet sich automatisch eine lokale HTML-Seite mit zwei Spalten. Jede Spalte zeigt Original-Transkription + optionale Übersetzung (DE/EN/Cebuano). Mikrofon per Button auf der Seite stummschaltbar.

---

## 1. Architektur-Überblick

```
Toggle-Taste gedrückt
    ↓
daemon.py → startet Dual-Recording + xdg-open http://localhost:8765/live
    ↓
RecordingSession(mic) + RecordingSession(desktop-monitor) — parallel
    ↓
WS /ws/live/mic      → 4s-Chunks → Whisper → Übersetzung → HTML
WS /ws/live/desktop  → 4s-Chunks → Whisper → Übersetzung → HTML
    ↓
GET /live → live.html (zwei Spalten, Ziel-Dropdown, Mic-Toggle)
```

### Neue Dateien

| Datei | Inhalt |
|-------|--------|
| `app/routes/live.py` | `GET /live`, `WS /ws/live/mic`, `WS /ws/live/desktop` |
| `app/translate.py` | GPT-4o-mini Übersetzungs-Wrapper |
| `app/static/live.html` | Frontend — zwei Spalten, Auto-Scroll, Dropdowns |

### Geänderte Dateien

| Datei | Änderung |
|-------|----------|
| `app/daemon.py` | Neuer "live"-Modus: Toggle-Key → Dual-Session starten/stoppen + Browser öffnen |
| `app/recorder.py` | `find_monitor_device() -> str \| None` — sucht PipeWire Monitor-Source |
| `app/main.py` | `live`-Router einbinden |

---

## 2. Audio-Capture

### PipeWire Monitor-Source

`find_monitor_device()` in `recorder.py`:
```python
def find_monitor_device() -> str | None:
    for dev in sd.query_devices():
        if "monitor" in dev["name"].lower() and dev["max_input_channels"] > 0:
            return dev["name"]
    return None
```

Gibt `None` zurück wenn kein Monitor-Gerät gefunden → Desktop-Spalte zeigt Fehlermeldung, Mic-Spalte läuft weiter.

### Dual-Stream-Betrieb

`daemon.py` hält zwei neue Instanzen:
- `_live_mic_session: RecordingSession | None`
- `_live_desktop_session: RecordingSession | None`

Toggle-Key im "live"-Modus:
- **Erster Druck:** beide Sessions starten, `xdg-open` Browser
- **Zweiter Druck:** beide Sessions stoppen, HTML zeigt "Aufnahme beendet"

Chunk-Intervall: **4 Sekunden** (identisch zum bestehenden Realtime-Muster).

### Mic-Toggle

WebSocket empfängt `{"mute": true}` vom Browser → `_live_mic_session.pause()` / `resume()`. Desktop-Session läuft unberührt weiter.

---

## 3. WebSocket-Endpoints (`app/routes/live.py`)

### `WS /ws/live/mic` und `WS /ws/live/desktop`

Beide Endpoints folgen dem gleichen Muster:

1. Chunk-WAV-Bytes empfangen von der jeweiligen Session
2. `transcribe_audio(wav_bytes, language="auto")` — Whisper erkennt Sprache automatisch
3. Falls `target_lang != detected_lang`: `translate(text, target_lang)` aufrufen
4. JSON senden:

```json
{
  "text": "Original-Text",
  "translation": "Übersetzter Text oder null",
  "speaker": "Sprecher 1",
  "lang_detected": "en",
  "timestamp": "08:42:15"
}
```

### Sprecher-Labels (Desktop-Stream)

Einfache Energie-basierte Erkennung:
- RMS-Energie jedes Chunks berechnen
- Bei >30% Abweichung zum Vorchunk → Sprecher-Label wechseln (1↔2)
- Label bleibt bei erstem Chunk "Sprecher 1"
- Mic-Stream hat kein Sprecher-Label (ist immer der Nutzer)

### Zielsprache-Steuerung

Browser sendet bei Dropdown-Änderung: `{"set_target_lang": "de"}` per WebSocket. Server speichert `target_lang` pro Connection.

---

## 4. Übersetzungs-Wrapper (`app/translate.py`)

```python
async def translate(text: str, target_lang: str) -> str:
    """Übersetzt text nach target_lang (de/en/ceb) via GPT-4o-mini.
    Gibt Originaltext zurück wenn Übersetzung fehlschlägt.
    """
```

- Nur aufgerufen wenn `target_lang != detected_lang`
- Prompt: `"Translate to {lang}. Return only the translation, no explanation."`
- Sprachen: `de` (Deutsch), `en` (English), `ceb` (Cebuano/Bisaya)
- Timeout: 5s — bei Überschreitung wird Originaltext angezeigt

---

## 5. Frontend (`app/static/live.html`)

### Layout

```
┌─────────────────────────────────────────────┐
│  🔴 Live  [Mic: AN ●]          08:42:15     │
├──────────────────────┬──────────────────────┤
│  🎤 Mikrofon         │  🔊 Unterhaltung     │
│  Ziel: [Deutsch ▾]  │  Ziel: [Deutsch ▾]   │
├──────────────────────┼──────────────────────┤
│  "Hello, can you..." │  Sprecher 1:         │
│  → Hallo, kannst du │  "Ja, das geht..."   │
│                      │  → Yes, that works  │
│  "I'll send it..."   │                      │
│  → Ich schicke es.. │  Sprecher 2:         │
│                      │  "Wann brauchst..."  │
│                      │  → When do you...   │
└──────────────────────┴──────────────────────┘
```

### Verhalten

- **Auto-Scroll:** Neuer Chunk scrollt automatisch nach unten
- **Ziel-Dropdown:** DE / EN / Cebuano — sendet `set_target_lang` per WS
- **Mic-Toggle:** Button oben — sendet `{"mute": true/false}`, zeigt AN●/AUS○
- **Verbindungsstatus:** Roter Punkt = live, grauer Punkt = getrennt
- **Kein Speichern** — Seite ist flüchtig, kein Persistenz-Layer
- **Zeitstempel** pro Chunk (HH:MM:SS)

### WebSocket-Verbindung

Zwei unabhängige WS-Verbindungen: `ws://localhost:8765/ws/live/mic` + `/ws/live/desktop`. Bei Verbindungsabbruch automatischer Reconnect alle 3s.

---

## 6. Daemon-Integration (`app/daemon.py`)

Neuer Toggle-Key-Modus "live" (separater Key, konfigurierbar in Settings):

```python
async def _toggle_live(self) -> None:
    if self._live_mic_session is None:
        # Starten
        monitor = find_monitor_device()
        self._live_desktop_session = RecordingSession(device=monitor) if monitor else None
        self._live_mic_session = RecordingSession(device=self.cfg.audio_device)
        self._live_mic_session.start()
        if self._live_desktop_session:
            self._live_desktop_session.start()
        subprocess.Popen(["xdg-open", "http://localhost:8765/live"])
    else:
        # Stoppen
        self._live_mic_session.stop()
        self._live_mic_session = None
        if self._live_desktop_session:
            self._live_desktop_session.stop()
            self._live_desktop_session = None
```

---

## 7. Fehlerbehandlung

| Situation | Verhalten |
|-----------|-----------|
| Kein Monitor-Gerät | Desktop-Spalte: "Kein Monitor-Gerät gefunden" — Mic läuft weiter |
| Übersetzung Timeout | Originaltext wird angezeigt, kein Fehler |
| WebSocket-Trennung | Browser reconnect alle 3s automatisch |
| `xdg-open` nicht verfügbar | Nur Log-Warnung, kein Crash — User öffnet manuell |
| Whisper-Fehler | Chunk wird übersprungen, nächster Chunk normal |

---

## 8. Out of Scope

- Aufzeichnung / Speichern der Transkription (kein Persistenz-Layer)
- Echte pyannote-Diarisierung (nur energie-basiert)
- Mehr als 3 Sprachen (DE/EN/CEB)
- Settings-Window Integration für Live-Key (manuell in config.json)
- Tab-spezifisches Audio (nur gesamter Desktop-Sound)

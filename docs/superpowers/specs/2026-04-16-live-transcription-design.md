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
LiveRecordingSession(mic)     → asyncio.Queue[bytes]  ──→ WS /ws/live/mic
LiveRecordingSession(desktop) → asyncio.Queue[bytes]  ──→ WS /ws/live/desktop
    ↓
Jeder WS-Handler: Queue.get() → Whisper (language=None) → Übersetzung → JSON → Browser
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
| `app/daemon.py` | `_toggle_live()` — exklusiver Live-Modus, startet/stoppt Dual-Session |
| `app/recorder.py` | `LiveRecordingSession` + `find_monitor_device()` |
| `app/config.py` | `live_key_codes: list[int]` + `live_key_name: str` in `BlitztextConfig` |
| `app/main.py` | `live`-Router einbinden |

---

## 2. Audio-Capture

### Neue Klasse: `LiveRecordingSession` (in `recorder.py`)

`RecordingSession` ist für Push-to-Talk gebaut — sie puffert alles bis `stop()`. Für Live-Streaming brauchen wir kontinuierliche 4s-Chunks. Daher neue Klasse:

```python
class LiveRecordingSession:
    """Streamt Audio in 4s-Chunks via asyncio.Queue."""

    CHUNK_SECONDS = 4

    def __init__(self, device: str | None = None) -> None:
        self._device = device
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._muted = False
        self._stream: sd.InputStream | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Startet den sounddevice-InputStream. loop für Queue-Thread-Safety."""

    def stop(self) -> None:
        """Stoppt den Stream, legt sentinel None in die Queue."""

    def set_muted(self, muted: bool) -> None:
        """Pausiert Audio-Chunks (schreibt Stille statt echtem Audio)."""

    @property
    def queue(self) -> asyncio.Queue[bytes]:
        """WS-Handler liest hieraus mit await queue.get(). None = Ende."""
```

Intern: `sd.InputStream` mit `blocksize = samplerate * CHUNK_SECONDS`. Callback akkumuliert Samples, konvertiert via `_to_wav_bytes()` (bestehende Hilfsfunktion in `recorder.py`) zu WAV-Bytes, füllt bei jedem vollen Chunk die Queue via `loop.call_soon_threadsafe`.

`set_muted(True)` schreibt Null-Bytes (Stille) statt echtem Mikrofon-Audio — kein Stop/Start, kein Audioglitch.

### `find_monitor_device()` in `recorder.py`

```python
def find_monitor_device() -> str | None:
    for dev in sd.query_devices():
        if "monitor" in dev["name"].lower() and dev["max_input_channels"] > 0:
            return dev["name"]
    return None
```

Gibt `None` zurück wenn kein Monitor-Gerät gefunden → Desktop-Spalte zeigt Fehlermeldung, Mic-Spalte läuft weiter.

### Exklusivität: Live-Modus vs. PTT-Modus

Live-Modus und normaler PTT-Modus sind **gegenseitig ausschließend**. `_toggle_live()` prüft `self._session is None` — wenn ein PTT-Recording läuft, wird Live-Start abgebrochen (Log-Warnung). Umgekehrt: PTT-Keys werden ignoriert während `_live_mic_session is not None`.

### Config: Live-Key

`BlitztextConfig` bekommt zwei neue Felder:
```python
live_key_codes: list[int] = Field(default_factory=list)
live_key_name: str = ""
```

Konfigurierbar wie andere Modes manuell in `~/.config/transcriptor/config.json`. Settings-Window-Integration ist Out of Scope.

---

## 3. WebSocket-Endpoints (`app/routes/live.py`)

### Modul-Level State

```python
_live_mic_session: LiveRecordingSession | None = None
_live_desktop_session: LiveRecordingSession | None = None
_mic_subscribers: set[asyncio.Queue[bytes | None]] = set()
_desktop_subscribers: set[asyncio.Queue[bytes | None]] = set()
```

`daemon.py` setzt `_live_mic_session` / `_live_desktop_session` via `live.set_sessions(mic, desktop)` beim Toggle.

**Fan-out / Multi-Client-Support:** Jeder WS-Connect erstellt eine eigene `asyncio.Queue` und trägt sich in `_mic_subscribers` / `_desktop_subscribers` ein. `LiveRecordingSession` liefert Chunks an das Router-Modul, das per `broadcast()` alle Subscriber-Queues befüllt. So können mehrere Browser-Tabs gleichzeitig verbunden sein ohne Datenverlust.

**Session-Lifetime:** Session-Objekte werden erst nach Empfang des `None`-Sentinels aus allen Subscriber-Queues freigegeben. `set_sessions(None, None)` markiert die Session nur als "gestoppt" — das Objekt bleibt bis zum letzten `queue.get() → None` im WS-Handler erreichbar.

### `WS /ws/live/mic` und `WS /ws/live/desktop`

Beide Endpoints folgen dem gleichen Muster:

```python
target_lang = "de"  # Default, pro Connection

# Empfange Steuer-Nachrichten vom Browser (non-blocking)
async def handle_client_messages(ws):
    async for msg in ws.iter_json():
        if "set_target_lang" in msg:
            nonlocal target_lang
            target_lang = msg["set_target_lang"]
        if "mute" in msg:  # nur Mic-Endpoint
            session.set_muted(msg["mute"])

# Parallel: Chunks aus Queue → Whisper → JSON → Browser
async def stream_chunks(ws, session, stream_type):
    while True:
        wav_bytes = await session.queue.get()
        if wav_bytes is None:  # Sentinel = Session beendet
            await ws.send_json({"done": True})
            break
        text, lang_detected = await _transcribe_with_lang(wav_bytes)
        translation = await translate(text, target_lang) if target_lang != lang_detected else None
        speaker = _detect_speaker(wav_bytes, stream_type)  # nur desktop
        await ws.send_json({
            "text": text,
            "translation": translation,
            "speaker": speaker,
            "lang_detected": lang_detected,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })
```

`asyncio.gather(handle_client_messages(...), stream_chunks(...))` — beide Tasks parallel.

**Reconnect-State:** Browser sendet nach Reconnect sofort `{"set_target_lang": "de"}` (gespeichert in `localStorage`). Kein Server-Side State nötig.

### Sprachdetektierung: `_transcribe_with_lang()`

Wrapper um `transcribe_audio()`:
- Online-Pfad: `response_format="verbose_json"` → gibt `(text, language)` zurück
- Lokaler Pfad: `faster_whisper` gibt `(segments, info)` zurück — `info.language` verwenden
- Gibt `(text, lang_detected)` als Tuple zurück

`transcribe_audio()` wird nicht verändert — `_transcribe_with_lang()` ist ein neuer Wrapper nur für Live-Endpoints.

**Sprachcode-Normalisierung:** Whisper gibt ISO 639-1/2-Codes zurück, die nicht immer BCP-47-kompatibel sind. Normalisierung vor dem `target_lang != lang_detected`-Vergleich:
```python
_LANG_NORMALIZE = {"cebuano": "ceb", "german": "de", "english": "en"}
lang_detected = _LANG_NORMALIZE.get(lang_detected, lang_detected)
```
Unbekannte Codes werden durchgereicht — Übersetzung wird dann immer aufgerufen (sicherer Fallback).

### Sprecher-Labels (Desktop-Stream)

RMS-Energie-basierte Heuristik — **grobe Annäherung, nicht zuverlässig bei unterschiedlicher Lautstärke desselben Sprechers**:
- RMS pro Chunk berechnen
- Bei >40% Abweichung zum gleitenden Mittelwert der letzten 3 Chunks → Label wechseln (1↔2)
- Mic-Stream: kein Sprecher-Label

### Zielsprache-Steuerung

Browser sendet bei Dropdown-Änderung: `{"set_target_lang": "de"}` per WebSocket. Server speichert `target_lang` als lokale Variable im WS-Handler (pro Connection, kein globaler State).

---

## 4. Übersetzungs-Wrapper (`app/translate.py`)

```python
async def translate(text: str, target_lang: str) -> str:
    """Übersetzt text nach target_lang (de/en/ceb) via GPT-4o-mini.
    Gibt Originaltext zurück wenn Übersetzung fehlschlägt oder Timeout.
    """
```

- Nur aufgerufen wenn `target_lang != detected_lang`
- Prompt: `"Translate to {lang_name}. Return only the translation, no explanation: {text}"`
- Sprachen: `de` (Deutsch), `en` (English), `ceb` (Cebuano/Bisaya)
- Timeout: 5s via `asyncio.wait_for` — bei Überschreitung Originaltext zurückgeben

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
- **Ziel-Dropdown:** DE / EN / Cebuano — sendet `{"set_target_lang": "de"}` per WS; Wert in `localStorage` gespeichert für Reconnect
- **Mic-Toggle:** Button oben — sendet `{"mute": true/false}`, zeigt AN●/AUS○
- **Verbindungsstatus:** Roter Punkt = live, grauer Punkt = getrennt
- **Kein Speichern** — Seite ist flüchtig, kein Persistenz-Layer
- **Zeitstempel** pro Chunk (HH:MM:SS)

### WebSocket-Reconnect

Zwei unabhängige WS-Verbindungen. Bei Verbindungsabbruch:
1. Reconnect alle 3s
2. Nach erfolgreicher Verbindung sofort `{"set_target_lang": localStorage.getItem("target_lang_mic")}` senden

---

## 6. Daemon-Integration (`app/daemon.py`)

### Neuer Toggle-Key "live"

`daemon.py` liest `cfg.live_key_codes` — wenn die Combo gedrückt wird und nicht im `cfg.modes`-Dict liegt:

```python
async def _toggle_live(self) -> None:
    # Exklusivität: PTT läuft gerade → kein Live-Start
    if self._session is not None:
        logger.warning("PTT läuft — Live-Modus nicht gestartet")
        return

    if self._live_mic_session is None:
        # Starten
        loop = asyncio.get_running_loop()
        monitor = find_monitor_device()
        mic = LiveRecordingSession(device=self.cfg.audio_device)
        desktop = LiveRecordingSession(device=monitor) if monitor else None
        mic.start(loop)
        if desktop:
            desktop.start(loop)
        self._live_mic_session = mic
        self._live_desktop_session = desktop
        live.set_sessions(mic, desktop)  # Router-Modul informieren
        subprocess.Popen(["xdg-open", "http://localhost:8765/live"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        # Stoppen
        self._live_mic_session.stop()
        self._live_mic_session = None
        if self._live_desktop_session:
            self._live_desktop_session.stop()
            self._live_desktop_session = None
        live.set_sessions(None, None)
```

PTT-Key-Handler prüft zusätzlich: `if self._live_mic_session is not None: continue` — PTT während Live ignoriert.

---

## 7. Fehlerbehandlung

| Situation | Verhalten |
|-----------|-----------|
| Kein Monitor-Gerät | Desktop-Spalte: "Kein Monitor-Gerät gefunden" — Mic läuft weiter |
| Übersetzung Timeout (5s) | Originaltext angezeigt, `translation: null` im JSON |
| WebSocket-Trennung | Browser reconnect alle 3s, sendet `set_target_lang` aus `localStorage` |
| `xdg-open` nicht verfügbar | Log-Warnung, kein Crash — User öffnet `localhost:8765/live` manuell |
| Whisper-Fehler | Chunk wird übersprungen (`continue`), nächster Chunk normal |
| PTT läuft bei Live-Start | Log-Warnung, Live-Start abgebrochen |
| Live läuft bei PTT-Druck | PTT-Event ignoriert |

---

## 8. Out of Scope

- Aufzeichnung / Speichern der Transkription
- Echte pyannote-Diarisierung (nur energie-basierte Heuristik)
- Mehr als 3 Sprachen (DE/EN/CEB)
- Settings-Window Integration für Live-Key (manuell in config.json)
- Tab-spezifisches Audio (nur gesamter Desktop-Sound)

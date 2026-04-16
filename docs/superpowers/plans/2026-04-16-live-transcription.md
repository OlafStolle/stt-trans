# Live-Transkription mit Übersetzung — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dual-Stream Live-Transkription (Mikrofon + Desktop-Audio) mit automatisch öffnender HTML-Seite, Übersetzung (DE/EN/Cebuano) und Mic-Toggle.

**Architecture:** `LiveRecordingSession` streamt 4s-WAV-Chunks via `asyncio.Queue` an zwei WebSocket-Endpoints in `app/routes/live.py`. Fan-out-Broadcast erlaubt mehrere Browser-Tabs. `daemon.py` öffnet via `xdg-open` den Browser beim Toggle-Key und koordiniert Live- vs. PTT-Modus (gegenseitig ausschließend).

**Tech Stack:** Python 3.12+, FastAPI, sounddevice/PipeWire, OpenAI Whisper API (verbose_json), GPT-4o-mini für Übersetzung, vanilla JS/HTML (kein Framework)

**Spec:** `docs/superpowers/specs/2026-04-16-live-transcription-design.md`
**Projekt:** `/mnt/data/Projects/blitztext-linux`

---

## Datei-Übersicht

| Datei | Aktion | Inhalt |
|-------|--------|--------|
| `app/config.py` | Modify | `live_key_codes: list[int]`, `live_key_name: str` zu `BlitztextConfig` |
| `app/recorder.py` | Modify | `LiveRecordingSession` + `find_monitor_device()` |
| `app/translate.py` | Create | `translate(text, target_lang) -> str` via GPT-4o-mini |
| `app/routes/live.py` | Create | `GET /live`, `WS /ws/live/mic`, `WS /ws/live/desktop`, broadcast, `_transcribe_with_lang()` |
| `app/static/live.html` | Create | Frontend: zwei Spalten, Dropdowns, Mic-Toggle, Auto-Scroll |
| `app/daemon.py` | Modify | `_toggle_live()`, Live-Key-Erkennung, PTT/Live-Exklusivität |
| `app/main.py` | Modify | `live`-Router einbinden |
| `tests/test_recorder.py` | Modify | Tests für `LiveRecordingSession` + `find_monitor_device()` |
| `tests/test_translate.py` | Create | Tests für `translate()` |
| `tests/test_live_routes.py` | Create | Tests für WS-Endpoints (mock sessions) |

---

## Task 1: Config — Live-Key-Felder

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Schreibe fehlschlagenden Test**

Füge am Ende von `tests/test_config.py` hinzu:

```python
def test_live_key_codes_default():
    cfg = BlitztextConfig(openai_api_key="x")
    assert cfg.live_key_codes == []

def test_live_key_name_default():
    cfg = BlitztextConfig(openai_api_key="x")
    assert cfg.live_key_name == ""

def test_live_key_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "cfg.json"))
    cfg = BlitztextConfig(openai_api_key="x", live_key_codes=[200], live_key_name="KEY_F17")
    save_config(cfg)
    loaded = load_config()
    assert loaded.live_key_codes == [200]
    assert loaded.live_key_name == "KEY_F17"
```

- [ ] **Step 2: Test ausführen — muss scheitern**

```bash
cd /mnt/data/Projects/blitztext-linux
python -m pytest tests/test_config.py::test_live_key_codes_default -v
```

Erwartet: `FAILED` — `BlitztextConfig has no field 'live_key_codes'`

- [ ] **Step 3: Felder zu BlitztextConfig hinzufügen**

In `app/config.py`, Klasse `BlitztextConfig`, nach `inject: InjectConfig`:

```python
    live_key_codes: list[int] = Field(default_factory=list)
    live_key_name: str = ""
```

- [ ] **Step 4: Tests ausführen — müssen bestehen**

```bash
python -m pytest tests/test_config.py -v
```

Erwartet: Alle Tests PASS (inkl. die 3 neuen)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: live_key_codes + live_key_name in BlitztextConfig"
```

---

## Task 2: recorder.py — find_monitor_device + LiveRecordingSession

**Files:**
- Modify: `app/recorder.py`
- Test: `tests/test_recorder.py`

- [ ] **Step 1: Schreibe fehlschlagende Tests**

Füge am Ende von `tests/test_recorder.py` hinzu:

```python
import asyncio
from unittest.mock import patch, MagicMock
from app.recorder import find_monitor_device, LiveRecordingSession


def test_find_monitor_device_found():
    fake_devices = [
        {"name": "Built-in Microphone", "max_input_channels": 2},
        {"name": "Monitor of Built-in Audio Analog Stereo", "max_input_channels": 2},
    ]
    with patch("sounddevice.query_devices", return_value=fake_devices):
        result = find_monitor_device()
    assert result == "Monitor of Built-in Audio Analog Stereo"


def test_find_monitor_device_not_found():
    fake_devices = [
        {"name": "Built-in Microphone", "max_input_channels": 2},
    ]
    with patch("sounddevice.query_devices", return_value=fake_devices):
        result = find_monitor_device()
    assert result is None


def test_find_monitor_device_no_input_channels():
    """Monitor-Gerät ohne Input-Channels wird ignoriert."""
    fake_devices = [
        {"name": "Monitor of Something", "max_input_channels": 0},
    ]
    with patch("sounddevice.query_devices", return_value=fake_devices):
        result = find_monitor_device()
    assert result is None


def test_live_recording_session_set_muted():
    """set_muted setzt intern _muted Flag."""
    session = LiveRecordingSession()
    assert session._muted is False
    session.set_muted(True)
    assert session._muted is True
    session.set_muted(False)
    assert session._muted is False


def test_live_recording_session_stop_puts_sentinel():
    """stop() legt None in Queue als Sentinel."""
    loop = asyncio.new_event_loop()
    try:
        async def _test():
            session = LiveRecordingSession()
            with patch("sounddevice.InputStream") as mock_stream_cls:
                mock_stream = MagicMock()
                mock_stream_cls.return_value = mock_stream
                session.start(loop)
                session.stop()
                sentinel = await asyncio.wait_for(session.queue.get(), timeout=1.0)
                assert sentinel is None
        loop.run_until_complete(_test())
    finally:
        loop.close()
```

- [ ] **Step 2: Tests ausführen — müssen scheitern**

```bash
python -m pytest tests/test_recorder.py::test_find_monitor_device_found -v
```

Erwartet: `FAILED` — `cannot import name 'find_monitor_device'`

- [ ] **Step 3: find_monitor_device implementieren**

Am Ende von `app/recorder.py` hinzufügen (vor `_to_wav_bytes`):

```python
def find_monitor_device() -> str | None:
    """Gibt den Namen des PipeWire Monitor-Source-Geräts zurück, oder None."""
    for dev in sd.query_devices():
        if "monitor" in dev["name"].lower() and dev["max_input_channels"] > 0:
            return dev["name"]
    return None
```

- [ ] **Step 4: LiveRecordingSession implementieren**

Am Ende von `app/recorder.py` hinzufügen:

```python
class LiveRecordingSession:
    """Streamt Audio kontinuierlich in 4s-WAV-Chunks via asyncio.Queue.

    Verwendung:
        session = LiveRecordingSession(device="default")
        session.start(asyncio.get_running_loop())
        # WS-Handler liest:
        while True:
            chunk = await session.queue.get()
            if chunk is None:
                break  # Session beendet
            # chunk ist WAV-Bytes
        session.stop()
    """

    CHUNK_SECONDS = 4

    def __init__(
        self,
        samplerate: int = SAMPLERATE,
        device: str | None = None,
    ) -> None:
        self.samplerate = samplerate
        self._device = device
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._muted = False
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer: list[np.ndarray] = []
        self._chunk_frames = samplerate * self.CHUNK_SECONDS

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Startet den sounddevice-InputStream. loop nötig für thread-sichere Queue."""
        self._loop = loop
        self._buffer.clear()
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=CHANNELS,
            dtype="int16",
            device=self._device or None,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        """PortAudio-Thread: Samples akkumulieren, bei vollem Chunk in Queue."""
        if self._muted:
            self._buffer.append(np.zeros_like(indata))
        else:
            self._buffer.append(indata.copy())

        total = sum(a.shape[0] for a in self._buffer)
        if total >= self._chunk_frames:
            audio = np.concatenate(self._buffer, axis=0)[: self._chunk_frames]
            self._buffer = []
            wav_bytes = _to_wav_bytes(audio, self.samplerate)
            if self._loop:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, wav_bytes)

    def stop(self) -> None:
        """Stoppt den Stream und legt None-Sentinel in die Queue."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._loop:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def set_muted(self, muted: bool) -> None:
        """Schaltet Mikrofon stumm (schreibt Stille statt echtem Audio)."""
        self._muted = muted

    @property
    def queue(self) -> asyncio.Queue[bytes | None]:
        """WS-Handler liest hieraus. None = Session beendet."""
        return self._queue
```

- [ ] **Step 5: Tests ausführen**

```bash
python -m pytest tests/test_recorder.py -v
```

Erwartet: Alle Tests PASS (inkl. die 5 neuen; ggf. 2 bestehende Tests mit echtem sounddevice überspringen falls kein Audio-Gerät vorhanden)

- [ ] **Step 6: Commit**

```bash
git add app/recorder.py tests/test_recorder.py
git commit -m "feat: LiveRecordingSession + find_monitor_device"
```

---

## Task 3: translate.py — GPT-4o-mini Übersetzungs-Wrapper

**Files:**
- Create: `app/translate.py`
- Create: `tests/test_translate.py`

- [ ] **Step 1: Schreibe fehlschlagenden Test**

Erstelle `tests/test_translate.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_translate_calls_gpt(monkeypatch):
    """translate() ruft GPT-4o-mini auf und gibt Übersetzung zurück."""
    mock_response = AsyncMock()
    mock_response.choices[0].message.content = "Hallo Welt"

    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client_fn.return_value = mock_client

        from app.translate import translate
        result = await translate("Hello world", "de")

    assert result == "Hallo Welt"


@pytest.mark.asyncio
async def test_translate_same_lang_skipped():
    """Wenn Zielsprache == Quellsprache, wird Originaltext zurückgegeben ohne API-Aufruf."""
    from app.translate import translate
    # Kein Mock nötig — soll frühzeitig zurückgeben
    # (Falls translate() das nicht prüft, wird dieser Test zeigen ob API aufgerufen wird)
    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client_fn.side_effect = AssertionError("Should not be called")
        result = await translate("Hallo", "de", source_lang="de")
    assert result == "Hallo"


@pytest.mark.asyncio
async def test_translate_timeout_returns_original(monkeypatch):
    """Bei Timeout gibt translate() den Originaltext zurück."""
    import asyncio

    async def slow_create(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = slow_create
        mock_client_fn.return_value = mock_client

        from app import translate as translate_module
        # Timeout auf 0.01s setzen für den Test
        original_timeout = translate_module.TRANSLATE_TIMEOUT
        translate_module.TRANSLATE_TIMEOUT = 0.01

        from app.translate import translate
        result = await translate("Hello", "de")
        translate_module.TRANSLATE_TIMEOUT = original_timeout

    assert result == "Hello"


@pytest.mark.asyncio
async def test_translate_cebuano():
    """Cebuano-Übersetzung funktioniert (prompt enthält 'Cebuano/Bisaya')."""
    mock_response = AsyncMock()
    mock_response.choices[0].message.content = "Kumusta kalibutan"

    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client_fn.return_value = mock_client

        from app.translate import translate
        result = await translate("Hello world", "ceb")

        call_kwargs = mock_client.chat.completions.create.call_args
        prompt = call_kwargs.kwargs["messages"][0]["content"]
        assert "Cebuano" in prompt or "Bisaya" in prompt

    assert result == "Kumusta kalibutan"
```

- [ ] **Step 2: Test ausführen — muss scheitern**

```bash
python -m pytest tests/test_translate.py::test_translate_calls_gpt -v
```

Erwartet: `FAILED` — `cannot import name 'translate'`

- [ ] **Step 3: translate.py implementieren**

Erstelle `app/translate.py`:

```python
# app/translate.py
"""GPT-4o-mini Übersetzungs-Wrapper für Live-Transkription."""
import asyncio
import logging

from openai import AsyncOpenAI
from app.config import load_config

logger = logging.getLogger("stt-trans.translate")

TRANSLATE_TIMEOUT = 5.0  # Sekunden

_LANG_NAMES = {
    "de": "German",
    "en": "English",
    "ceb": "Cebuano/Bisaya",
}

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client


async def translate(
    text: str,
    target_lang: str,
    source_lang: str | None = None,
) -> str:
    """Übersetzt text in target_lang (de/en/ceb) via GPT-4o-mini.

    Gibt Originaltext zurück bei:
    - source_lang == target_lang
    - Timeout (>5s)
    - API-Fehler

    Args:
        text: Zu übersetzender Text.
        target_lang: Zielsprache: "de", "en" oder "ceb".
        source_lang: Quellsprache (optional, für Kurzschluss-Check).
    """
    if source_lang and source_lang == target_lang:
        return text
    if not text.strip():
        return text

    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    prompt = f"Translate to {lang_name}. Return only the translation, no explanation: {text}"

    try:
        client = get_openai_client()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0,
            ),
            timeout=TRANSLATE_TIMEOUT,
        )
        return response.choices[0].message.content.strip()
    except asyncio.TimeoutError:
        logger.warning("translate: timeout nach %.1fs — Originaltext zurückgegeben", TRANSLATE_TIMEOUT)
        return text
    except Exception as e:
        logger.error("translate: Fehler: %s", e)
        return text
```

- [ ] **Step 4: Tests ausführen**

```bash
python -m pytest tests/test_translate.py -v
```

Erwartet: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/translate.py tests/test_translate.py
git commit -m "feat: translate() wrapper — GPT-4o-mini, DE/EN/CEB, timeout-safe"
```

---

## Task 4: routes/live.py — WebSocket-Endpoints + Broadcast

**Files:**
- Create: `app/routes/live.py`
- Create: `tests/test_live_routes.py`

- [ ] **Step 1: Schreibe fehlschlagende Tests**

Erstelle `tests/test_live_routes.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.routes import live as live_module


def test_broadcast_delivers_to_all_subscribers():
    """broadcast() liefert Bytes an alle Subscriber-Queues."""
    loop = asyncio.new_event_loop()
    try:
        async def _test():
            live_module._mic_subscribers.clear()
            q1: asyncio.Queue = asyncio.Queue()
            q2: asyncio.Queue = asyncio.Queue()
            live_module._mic_subscribers.add(q1)
            live_module._mic_subscribers.add(q2)
            await live_module._broadcast(live_module._mic_subscribers, b"wav_data")
            assert await asyncio.wait_for(q1.get(), timeout=1.0) == b"wav_data"
            assert await asyncio.wait_for(q2.get(), timeout=1.0) == b"wav_data"
        loop.run_until_complete(_test())
    finally:
        live_module._mic_subscribers.clear()
        loop.close()


def test_set_sessions_stores_sessions():
    """set_sessions() speichert Sessions im Modul-State."""
    mock_mic = MagicMock()
    mock_desktop = MagicMock()
    live_module.set_sessions(mock_mic, mock_desktop)
    assert live_module._live_mic_session is mock_mic
    assert live_module._live_desktop_session is mock_desktop
    live_module.set_sessions(None, None)
    assert live_module._live_mic_session is None


def test_lang_normalize():
    """_normalize_lang() normalisiert Whisper-Codes korrekt."""
    from app.routes.live import _normalize_lang
    assert _normalize_lang("german") == "de"
    assert _normalize_lang("english") == "en"
    assert _normalize_lang("cebuano") == "ceb"
    assert _normalize_lang("de") == "de"
    assert _normalize_lang("unknown_code") == "unknown_code"


def test_detect_speaker_changes_on_energy_shift():
    """_detect_speaker() wechselt Sprecher bei großem RMS-Shift."""
    import numpy as np
    import io
    import scipy.io.wavfile as wavfile
    from app.routes.live import _SpeakerDetector

    detector = _SpeakerDetector()

    def make_wav(amplitude: int) -> bytes:
        audio = np.full((16000 * 4, 1), amplitude, dtype=np.int16)
        buf = io.BytesIO()
        wavfile.write(buf, 16000, audio)
        return buf.getvalue()

    # Erster Chunk: Sprecher 1
    label1 = detector.detect(make_wav(1000))
    assert label1 == "Sprecher 1"

    # Ähnliche Energie: kein Wechsel
    label2 = detector.detect(make_wav(1100))
    assert label2 == "Sprecher 1"

    # Sehr lauter Chunk: Sprecher-Wechsel
    label3 = detector.detect(make_wav(20000))
    assert label3 == "Sprecher 2"
```

- [ ] **Step 2: Tests ausführen — müssen scheitern**

```bash
python -m pytest tests/test_live_routes.py::test_set_sessions_stores_sessions -v
```

Erwartet: `FAILED` — `cannot import name 'live'`

- [ ] **Step 3: routes/live.py implementieren**

Erstelle `app/routes/live.py`:

```python
# app/routes/live.py
"""Live-Transkription WebSocket-Endpoints + HTML-Seite."""
import asyncio
import io
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.recorder import LiveRecordingSession
from app.translate import translate

logger = logging.getLogger("stt-trans.live")

router = APIRouter()

# ---------------------------------------------------------------------------
# Modul-Level State
# ---------------------------------------------------------------------------

_live_mic_session: LiveRecordingSession | None = None
_live_desktop_session: LiveRecordingSession | None = None
_mic_subscribers: set[asyncio.Queue] = set()
_desktop_subscribers: set[asyncio.Queue] = set()

_LANG_NORMALIZE = {
    "german": "de",
    "english": "en",
    "cebuano": "ceb",
    "bisaya": "ceb",
}

_STATIC_DIR = Path(__file__).parent.parent / "static"


# ---------------------------------------------------------------------------
# Public API (für daemon.py)
# ---------------------------------------------------------------------------

def set_sessions(
    mic: LiveRecordingSession | None,
    desktop: LiveRecordingSession | None,
) -> None:
    """Daemon setzt aktive Sessions. Nur Speicherung — keine Tasks starten.
    Daemon ruft anschließend asyncio.create_task(live.start_pumps()) auf.
    """
    global _live_mic_session, _live_desktop_session
    _live_mic_session = mic
    _live_desktop_session = desktop


async def start_pumps() -> None:
    """Startet Pump-Tasks. Muss aus async-Kontext aufgerufen werden (via create_task)."""
    if _live_mic_session:
        asyncio.create_task(_pump_session(_live_mic_session, _mic_subscribers))
    if _live_desktop_session:
        asyncio.create_task(_pump_session(_live_desktop_session, _desktop_subscribers))


# ---------------------------------------------------------------------------
# Interne Helpers
# ---------------------------------------------------------------------------

def _normalize_lang(lang: str) -> str:
    return _LANG_NORMALIZE.get(lang.lower(), lang)


async def _broadcast(subscribers: set[asyncio.Queue], data: bytes | None) -> None:
    """Liefert data an alle Subscriber-Queues."""
    for q in list(subscribers):
        await q.put(data)


async def _pump_session(
    session: LiveRecordingSession,
    subscribers: set[asyncio.Queue],
) -> None:
    """Liest Chunks aus Session-Queue und broadcastet an alle Subscriber."""
    while True:
        chunk = await session.queue.get()
        await _broadcast(subscribers, chunk)
        if chunk is None:
            break


async def _transcribe_with_lang(wav_bytes: bytes) -> tuple[str, str]:
    """Transkribiert WAV-Bytes und gibt (text, lang_detected) zurück.

    Verwendet verbose_json für Sprachdetektierung (Online-Pfad).
    Lokaler Pfad: faster-whisper info.language.
    """
    from app.config import load_config
    cfg = load_config()

    if cfg.transcribe_backend == "local":
        try:
            from faster_whisper import WhisperModel
            from app.transcribe import _fw_engine
            loop = asyncio.get_running_loop()

            def _run():
                import tempfile, os
                _fw_engine.ensure_loaded(cfg.local_whisper_model)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav_bytes)
                    tmp_path = tmp.name
                try:
                    segments, info = _fw_engine._model.transcribe(tmp_path, language=None)
                    text = " ".join(seg.text.strip() for seg in segments).strip()
                    return text, _normalize_lang(info.language or "")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

            return await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.warning("Local transcribe failed, falling back to online: %s", e)

    # Online-Pfad mit verbose_json
    from app.transcribe import get_client
    import io as _io
    client = get_client()
    buf = _io.BytesIO(wav_bytes)
    buf.name = "audio.wav"
    result = await client.audio.transcriptions.create(
        model="whisper-1",
        file=buf,
        response_format="verbose_json",
    )
    lang = _normalize_lang(getattr(result, "language", "") or "")
    text = getattr(result, "text", "") or ""
    return text.strip(), lang


class _SpeakerDetector:
    """Einfache RMS-basierte Sprecher-Heuristik (wechselt bei >40% RMS-Shift)."""

    THRESHOLD = 0.40
    HISTORY = 3

    def __init__(self) -> None:
        self._current = "Sprecher 1"
        self._rms_history: list[float] = []

    def detect(self, wav_bytes: bytes) -> str:
        rms = self._compute_rms(wav_bytes)
        if self._rms_history:
            avg = sum(self._rms_history[-self.HISTORY:]) / min(len(self._rms_history), self.HISTORY)
            if avg > 0 and abs(rms - avg) / avg > self.THRESHOLD:
                self._current = "Sprecher 2" if self._current == "Sprecher 1" else "Sprecher 1"
        self._rms_history.append(rms)
        return self._current

    @staticmethod
    def _compute_rms(wav_bytes: bytes) -> float:
        try:
            _, data = wavfile.read(io.BytesIO(wav_bytes))
            return float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# HTTP Route
# ---------------------------------------------------------------------------

@router.get("/live")
async def live_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "live.html")


# ---------------------------------------------------------------------------
# WebSocket Routes
# ---------------------------------------------------------------------------

@router.websocket("/ws/live/mic")
async def ws_live_mic(ws: WebSocket) -> None:
    await ws.accept()
    if _live_mic_session is None:
        await ws.send_json({"error": "Keine aktive Live-Session"})
        await ws.close()
        return
    my_queue: asyncio.Queue = asyncio.Queue()
    _mic_subscribers.add(my_queue)
    target_lang = "de"
    muted = False

    async def handle_client() -> None:
        nonlocal target_lang, muted
        try:
            async for msg in ws.iter_json():
                if "set_target_lang" in msg:
                    target_lang = msg["set_target_lang"]
                if "mute" in msg:
                    muted = bool(msg["mute"])
                    if _live_mic_session:
                        _live_mic_session.set_muted(muted)
        except (WebSocketDisconnect, Exception):
            pass

    async def stream() -> None:
        try:
            while True:
                wav_bytes = await my_queue.get()
                if wav_bytes is None:
                    await ws.send_json({"done": True})
                    break
                try:
                    text, lang_detected = await _transcribe_with_lang(wav_bytes)
                    if not text:
                        continue
                    translation = None
                    if target_lang and target_lang != lang_detected:
                        translation = await translate(text, target_lang, source_lang=lang_detected)
                    await ws.send_json({
                        "text": text,
                        "translation": translation,
                        "speaker": None,
                        "lang_detected": lang_detected,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                    })
                except Exception as e:
                    logger.error("mic stream error: %s", e)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.gather(handle_client(), stream())
    finally:
        _mic_subscribers.discard(my_queue)


@router.websocket("/ws/live/desktop")
async def ws_live_desktop(ws: WebSocket) -> None:
    await ws.accept()
    if _live_desktop_session is None:
        msg = "Kein Monitor-Gerät gefunden" if _live_mic_session else "Keine aktive Live-Session"
        await ws.send_json({"error": msg})
        await ws.close()
        return
    my_queue: asyncio.Queue = asyncio.Queue()
    _desktop_subscribers.add(my_queue)
    target_lang = "de"
    detector = _SpeakerDetector()

    async def handle_client() -> None:
        nonlocal target_lang
        try:
            async for msg in ws.iter_json():
                if "set_target_lang" in msg:
                    target_lang = msg["set_target_lang"]
        except (WebSocketDisconnect, Exception):
            pass

    async def stream() -> None:
        try:
            while True:
                wav_bytes = await my_queue.get()
                if wav_bytes is None:
                    await ws.send_json({"done": True})
                    break
                try:
                    text, lang_detected = await _transcribe_with_lang(wav_bytes)
                    if not text:
                        continue
                    translation = None
                    if target_lang and target_lang != lang_detected:
                        translation = await translate(text, target_lang, source_lang=lang_detected)
                    speaker = detector.detect(wav_bytes)
                    await ws.send_json({
                        "text": text,
                        "translation": translation,
                        "speaker": speaker,
                        "lang_detected": lang_detected,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                    })
                except Exception as e:
                    logger.error("desktop stream error: %s", e)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.gather(handle_client(), stream())
    finally:
        _desktop_subscribers.discard(my_queue)
```

- [ ] **Step 4: Tests ausführen**

```bash
python -m pytest tests/test_live_routes.py -v
```

Erwartet: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/live.py tests/test_live_routes.py
git commit -m "feat: live WS endpoints — broadcast, speaker detection, transcribe_with_lang"
```

---

## Task 5: live.html — Frontend

**Files:**
- Create: `app/static/live.html`

Kein automatisierter Test — manuell verifizieren.

- [ ] **Step 1: live.html erstellen**

Erstelle `app/static/live.html`:

```html
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Live-Transkription</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f0f11; color: #e0e0e0; height: 100vh; display: flex;
         flex-direction: column; overflow: hidden; }

  /* Header */
  #header { display: flex; align-items: center; gap: 12px; padding: 10px 16px;
            background: #1a1a1f; border-bottom: 1px solid #2a2a35; flex-shrink: 0; }
  #status-dot { width: 10px; height: 10px; border-radius: 50%; background: #888; }
  #status-dot.live { background: #ef4444; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.4 } }
  #status-text { font-size: 13px; font-weight: 600; letter-spacing: .05em; }
  #mic-btn { margin-left: auto; padding: 5px 14px; border-radius: 6px; border: none;
             cursor: pointer; font-size: 12px; font-weight: 600;
             background: #22c55e; color: #fff; transition: background .2s; }
  #mic-btn.muted { background: #ef4444; }
  #clock { font-size: 12px; color: #666; margin-left: 8px; }

  /* Columns */
  #columns { display: flex; flex: 1; overflow: hidden; }
  .col { flex: 1; display: flex; flex-direction: column; overflow: hidden;
         border-right: 1px solid #2a2a35; }
  .col:last-child { border-right: none; }

  /* Column header */
  .col-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
               background: #16161b; border-bottom: 1px solid #2a2a35; flex-shrink: 0; }
  .col-title { font-size: 12px; font-weight: 700; color: #a0a0b0;
               text-transform: uppercase; letter-spacing: .08em; flex: 1; }
  .col-header select { background: #2a2a35; color: #e0e0e0; border: 1px solid #3a3a45;
                       border-radius: 4px; padding: 3px 6px; font-size: 11px; cursor: pointer; }
  .ws-dot { width: 7px; height: 7px; border-radius: 50%; background: #888; flex-shrink: 0; }
  .ws-dot.connected { background: #22c55e; }

  /* Transcript area */
  .transcript { flex: 1; overflow-y: auto; padding: 12px; }
  .chunk { margin-bottom: 14px; }
  .chunk-meta { font-size: 10px; color: #555; margin-bottom: 3px; }
  .chunk-speaker { font-size: 10px; color: #7c7cff; font-weight: 600; margin-bottom: 2px; }
  .chunk-text { font-size: 13px; line-height: 1.5; color: #d0d0e0; }
  .chunk-translation { font-size: 12px; color: #88cc88; margin-top: 2px; padding-left: 8px;
                       border-left: 2px solid #2a4a2a; }
  .no-signal { font-size: 12px; color: #444; padding: 20px; text-align: center; }
</style>
</head>
<body>

<div id="header">
  <div id="status-dot"></div>
  <span id="status-text">Warte auf Verbindung…</span>
  <button id="mic-btn" onclick="toggleMic()">🎤 Mic: AN</button>
  <span id="clock"></span>
</div>

<div id="columns">
  <div class="col">
    <div class="col-header">
      <span class="col-title">🎤 Mikrofon</span>
      <select id="mic-lang" onchange="setLang('mic', this.value)">
        <option value="de">Deutsch</option>
        <option value="en">English</option>
        <option value="ceb">Cebuano</option>
      </select>
      <div class="ws-dot" id="mic-ws-dot"></div>
    </div>
    <div class="transcript" id="mic-transcript">
      <div class="no-signal">Warte auf Transkription…</div>
    </div>
  </div>
  <div class="col">
    <div class="col-header">
      <span class="col-title">🔊 Unterhaltung</span>
      <select id="desktop-lang" onchange="setLang('desktop', this.value)">
        <option value="de">Deutsch</option>
        <option value="en">English</option>
        <option value="ceb">Cebuano</option>
      </select>
      <div class="ws-dot" id="desktop-ws-dot"></div>
    </div>
    <div class="transcript" id="desktop-transcript">
      <div class="no-signal">Warte auf Transkription…</div>
    </div>
  </div>
</div>

<script>
const WS_BASE = `ws://${location.host}`;
let micWs = null, desktopWs = null;
let micMuted = false;
let firstMicChunk = true, firstDesktopChunk = true;

// Clock
setInterval(() => {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('de-DE');
}, 1000);

// Lang from localStorage
['mic', 'desktop'].forEach(stream => {
  const saved = localStorage.getItem(`live_lang_${stream}`);
  if (saved) document.getElementById(`${stream}-lang`).value = saved;
});

function setLang(stream, lang) {
  localStorage.setItem(`live_lang_${stream}`, lang);
  const ws = stream === 'mic' ? micWs : desktopWs;
  if (ws && ws.readyState === WebSocket.OPEN)
    ws.send(JSON.stringify({ set_target_lang: lang }));
}

function toggleMic() {
  micMuted = !micMuted;
  const btn = document.getElementById('mic-btn');
  btn.textContent = micMuted ? '🔇 Mic: AUS' : '🎤 Mic: AN';
  btn.className = micMuted ? 'muted' : '';
  if (micWs && micWs.readyState === WebSocket.OPEN)
    micWs.send(JSON.stringify({ mute: micMuted }));
}

function appendChunk(containerId, data) {
  const container = document.getElementById(containerId);
  // Entferne "Warte…" Platzhalter
  const placeholder = container.querySelector('.no-signal');
  if (placeholder) placeholder.remove();

  const div = document.createElement('div');
  div.className = 'chunk';
  let html = '';
  if (data.speaker)
    html += `<div class="chunk-speaker">${data.speaker}</div>`;
  html += `<div class="chunk-meta">${data.timestamp}${data.lang_detected ? ' · ' + data.lang_detected : ''}</div>`;
  html += `<div class="chunk-text">${escHtml(data.text)}</div>`;
  if (data.translation)
    html += `<div class="chunk-translation">→ ${escHtml(data.translation)}</div>`;
  div.innerHTML = html;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function updateStatus(connected) {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  if (connected) {
    dot.className = 'live';
    text.textContent = '🔴 Live';
  } else {
    dot.className = '';
    text.textContent = 'Verbindung getrennt…';
  }
}

function connectWs(stream) {
  const url = `${WS_BASE}/ws/live/${stream}`;
  const ws = new WebSocket(url);
  const dot = document.getElementById(`${stream}-ws-dot`);

  ws.onopen = () => {
    dot.className = 'ws-dot connected';
    updateStatus(true);
    // Zielsprache nach Reconnect senden
    const lang = localStorage.getItem(`live_lang_${stream}`) || 'de';
    ws.send(JSON.stringify({ set_target_lang: lang }));
    if (stream === 'mic' && micMuted)
      ws.send(JSON.stringify({ mute: true }));
  };

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.done) return;
    appendChunk(`${stream}-transcript`, data);
  };

  ws.onclose = () => {
    dot.className = 'ws-dot';
    updateStatus(false);
    setTimeout(() => connectWs(stream), 3000);
  };

  ws.onerror = () => ws.close();

  if (stream === 'mic') micWs = ws;
  else desktopWs = ws;
}

connectWs('mic');
connectWs('desktop');
</script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add app/static/live.html
git commit -m "feat: live.html — dual-column live transcription frontend"
```

---

## Task 6: daemon.py — Live-Key-Integration

**Files:**
- Modify: `app/daemon.py`
- Test: `tests/test_daemon.py` (neue Tests ergänzen)

- [ ] **Step 1: Schreibe fehlschlagende Tests**

Öffne `tests/test_daemon.py`. Füge neue Tests hinzu (nach bestehenden):

```python
def test_toggle_live_blocked_during_ptt(monkeypatch):
    """_toggle_live() startet nicht wenn PTT-Session läuft."""
    import asyncio
    from unittest.mock import MagicMock, patch, AsyncMock
    from app.daemon import BlitztextDaemon
    from app.config import BlitztextConfig

    cfg = BlitztextConfig(openai_api_key="x", input_device="/dev/null")
    daemon = BlitztextDaemon(cfg)
    daemon._session = MagicMock()  # PTT läuft

    with patch("app.daemon.LiveRecordingSession") as mock_cls:
        asyncio.run(daemon._toggle_live())
        mock_cls.assert_not_called()


def test_toggle_live_starts_sessions(monkeypatch):
    """_toggle_live() startet mic + desktop Session wenn frei."""
    import asyncio
    from unittest.mock import MagicMock, patch
    from app.daemon import BlitztextDaemon
    from app.config import BlitztextConfig

    cfg = BlitztextConfig(openai_api_key="x", input_device="/dev/null",
                          live_key_codes=[200])
    daemon = BlitztextDaemon(cfg)

    mock_mic = MagicMock()
    mock_desktop = MagicMock()

    with patch("app.daemon.LiveRecordingSession", side_effect=[mock_mic, mock_desktop]), \
         patch("app.daemon.find_monitor_device", return_value="Monitor Device"), \
         patch("app.daemon.live") as mock_live, \
         patch("subprocess.Popen"):
        mock_live.start_pumps = AsyncMock()
        asyncio.run(daemon._toggle_live())

    assert daemon._live_mic_session is mock_mic
    assert daemon._live_desktop_session is mock_desktop
    mock_mic.start.assert_called_once()
    mock_desktop.start.assert_called_once()
    mock_live.set_sessions.assert_called_once_with(mock_mic, mock_desktop)


def test_toggle_live_stops_sessions(monkeypatch):
    """Zweiter _toggle_live()-Aufruf stoppt Sessions."""
    import asyncio
    from unittest.mock import MagicMock, patch
    from app.daemon import BlitztextDaemon
    from app.config import BlitztextConfig

    cfg = BlitztextConfig(openai_api_key="x", live_key_codes=[200])
    daemon = BlitztextDaemon(cfg)
    mock_mic = MagicMock()
    mock_desktop = MagicMock()
    daemon._live_mic_session = mock_mic
    daemon._live_desktop_session = mock_desktop

    with patch("app.daemon.live") as mock_live:
        asyncio.run(daemon._toggle_live())

    mock_mic.stop.assert_called_once()
    mock_desktop.stop.assert_called_once()
    assert daemon._live_mic_session is None
    mock_live.set_sessions.assert_called_with(None, None)
```

- [ ] **Step 2: Tests ausführen — müssen scheitern**

```bash
python -m pytest tests/test_daemon.py::test_toggle_live_blocked_during_ptt -v
```

Erwartet: `FAILED` — `BlitztextDaemon has no attribute '_toggle_live'`

- [ ] **Step 3: daemon.py erweitern**

In `app/daemon.py`:

**Imports ergänzen** (oben):
```python
import subprocess
from app.recorder import LiveRecordingSession, find_monitor_device
from app.routes import live
```

**In `__init__`** nach `self._pressed_keys`:
```python
        self._live_mic_session: LiveRecordingSession | None = None
        self._live_desktop_session: LiveRecordingSession | None = None
```

**Neue Methode** nach `_key_to_mode_combo()`:
```python
    async def _toggle_live(self) -> None:
        """Startet oder stoppt den Live-Transkriptions-Modus."""
        if self._session is not None:
            logger.warning("PTT läuft — Live-Modus nicht gestartet")
            return

        if self._live_mic_session is None:
            loop = asyncio.get_running_loop()
            monitor = find_monitor_device()
            audio_device = self.cfg.audio_device if self.cfg.audio_device != "default" else None
            mic = LiveRecordingSession(device=audio_device)
            desktop = LiveRecordingSession(device=monitor) if monitor else None
            mic.start(loop)
            if desktop:
                desktop.start(loop)
            self._live_mic_session = mic
            self._live_desktop_session = desktop
            live.set_sessions(mic, desktop)
            asyncio.create_task(live.start_pumps())
            notify("recording", "Live-Transkription gestartet")
            try:
                subprocess.Popen(
                    ["xdg-open", "http://localhost:8765/live"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                logger.warning("xdg-open nicht gefunden — öffne http://localhost:8765/live manuell")
        else:
            self._live_mic_session.stop()
            self._live_mic_session = None
            if self._live_desktop_session:
                self._live_desktop_session.stop()
                self._live_desktop_session = None
            live.set_sessions(None, None)
            notify("done", "Live-Transkription beendet")
```

**Im `run()` Event-Loop**, nach `self._pressed_keys.add(event.code)` im `event.value == 1`-Block:

Ersetze die Zeile:
```python
                    mode_name = self._key_to_mode_combo()
                    if mode_name is None:
                        continue
```

Durch:
```python
                    # Live-Key prüfen
                    live_combo = set(self.cfg.live_key_codes)
                    if live_combo and live_combo <= self._pressed_keys:
                        asyncio.create_task(self._toggle_live())
                        continue

                    # PTT während Live ignorieren
                    if self._live_mic_session is not None:
                        continue

                    mode_name = self._key_to_mode_combo()
                    if mode_name is None:
                        continue
```

**In `stop()`**:
```python
    def stop(self) -> None:
        self._running = False
        if self._session:
            self._session.stop()
        if self._live_mic_session:
            self._live_mic_session.stop()
        if self._live_desktop_session:
            self._live_desktop_session.stop()
```

- [ ] **Step 4: Tests ausführen**

```bash
python -m pytest tests/test_daemon.py -v
```

Erwartet: Alle neuen Tests PASS (existierende Tests ggf. mit Sounddevice-Skip)

- [ ] **Step 5: Commit**

```bash
git add app/daemon.py tests/test_daemon.py
git commit -m "feat: daemon live-toggle — _toggle_live(), PTT/Live exklusiv, xdg-open"
```

---

## Task 7: main.py — Live-Router einbinden

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Router importieren und einbinden**

In `app/main.py`:

Ergänze Import:
```python
from app.routes.live import router as live_router
```

In `create_app()` nach `app.include_router(status_router)`:
```python
    app.include_router(live_router)
```

- [ ] **Step 2: Import-Check**

```bash
cd /mnt/data/Projects/blitztext-linux
python -c "from app.main import create_app; app = create_app(); print('OK')"
```

Erwartet: `OK`

- [ ] **Step 3: Alle Tests ausführen**

```bash
python -m pytest tests/ -v --ignore=tests/test_recorder.py 2>&1 | tail -20
```

Erwartet: Neue Tests grün, keine neuen Fehler

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: include live router in main app"
```

---

## Task 8: Service neustarten + Live-Key konfigurieren

- [ ] **Step 1: Service neustarten**

```bash
systemctl --user restart stt-trans.service
sleep 2
systemctl --user status stt-trans.service | head -5
```

- [ ] **Step 2: Live-Key in config.json setzen**

Öffne `~/.config/transcriptor/config.json`, füge hinzu (oder passe an):
```json
"live_key_codes": [200],
"live_key_name": "KEY_F17"
```

Den richtigen Key-Code ermitteln: Tastendruck in den Einstellungen erkennen und aus dem Settings-Window ablesen.

- [ ] **Step 3: Verifikation**

```bash
# 1. Endpoint erreichbar
curl -s http://localhost:8765/live | head -5
# Erwartet: HTML-Inhalt

# 2. Import-Check
cd /mnt/data/Projects/blitztext-linux
python -c "
from app.routes.live import _normalize_lang, _SpeakerDetector
print('normalize de:', _normalize_lang('german'))
print('normalize ceb:', _normalize_lang('cebuano'))
d = _SpeakerDetector()
print('Speaker init:', d._current)
print('OK')
"
# Erwartet: normalize de: de / normalize ceb: ceb / Speaker init: Sprecher 1 / OK

# 3. Alle Tests
python -m pytest tests/test_config.py tests/test_translate.py tests/test_live_routes.py tests/test_daemon.py -v 2>&1 | tail -10
```

---

## Abschluss-Checkliste

- [ ] `live_key_codes` in config.json gesetzt und Service neu gestartet
- [ ] Toggle-Key drücken → Browser öffnet `localhost:8765/live`
- [ ] Beide Spalten zeigen Transkriptionen
- [ ] Dropdown-Wechsel → Übersetzung erscheint in Zeile darunter
- [ ] Mic-Toggle-Button schaltet Mikrofon stumm
- [ ] Service-Log zeigt keine Fehler: `journalctl --user -u stt-trans.service -f`

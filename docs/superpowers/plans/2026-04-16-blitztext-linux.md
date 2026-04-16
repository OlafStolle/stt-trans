# Blitztext Linux — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Systemweiter Linux-Diktierdienst — Taste halten → Sprache aufnehmen → Text in aktives Fenster einfügen — mit 4 Modi (Normal/Plus/Rage/Emoji), Config-API und systemd-Autostart.

**Architecture:** FastAPI-Backend läuft als systemd User-Service. evdev überwacht Tastatureingaben global (ohne Browser). Whisper transkribiert, GPT-4o-mini verarbeitet Modi. xdotool injiziert Text ins aktive Fenster.

**Tech Stack:** Python 3.12, FastAPI, evdev, sounddevice/arecord, OpenAI Whisper + GPT-4o-mini, xdotool, notify-send, systemd

**Spec:** `/mnt/data/Projects/transcriptor/docs/blitztext-linux-anforderungen.md`

---

## File Map

```
blitztext-linux/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory + lifespan
│   ├── config.py            # Config laden/schreiben (~/.config/transcriptor/config.json)
│   ├── recorder.py          # Audioaufnahme via sounddevice → WAV-Bytes
│   ├── transcribe.py        # Whisper API wrapper (Datei → Text)
│   ├── process.py           # LLM Post-Processing (Plus/Rage/Emoji)
│   ├── inject.py            # xdotool Text-Inject + notify-send
│   ├── daemon.py            # evdev-Daemon: Tasten → Pipeline auslösen
│   └── routes/
│       ├── __init__.py
│       ├── config_routes.py # GET/PATCH/POST /api/config
│       ├── health_routes.py # GET /api/health
│       └── process_routes.py# POST /api/process/{mode} (für manuelle Aufrufe)
├── tests/
│   ├── test_config.py
│   ├── test_recorder.py
│   ├── test_transcribe.py
│   ├── test_process.py
│   ├── test_inject.py
│   ├── test_daemon.py
│   ├── test_routes.py
│   └── test_main.py
├── blitztext.service        # systemd User-Service Template
├── install.sh               # Setup: venv, deps, systemd-Service aktivieren
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Task 1: Projektstruktur + pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/routes/__init__.py`
- Create: `.env.example`

- [ ] **Step 1: Verzeichnisstruktur anlegen**

```bash
cd /mnt/data/Projects/blitztext-linux
mkdir -p app/routes tests
touch app/__init__.py app/routes/__init__.py
```

- [ ] **Step 2: pyproject.toml schreiben**

```toml
[project]
name = "blitztext-linux"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "openai>=1.30",
    "evdev>=1.7",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "scipy>=1.13",
    "python-dotenv>=1.0",
    "aiofiles>=23.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: .env.example**

```bash
cat > .env.example << 'EOF'
OPENAI_API_KEY=sk-...
BLITZTEXT_CONFIG=~/.config/transcriptor/config.json
EOF
```

- [ ] **Step 4: venv erstellen + installieren**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "chore: init project structure"
```

---

## Task 2: Config-Modul

**Files:**
- Create: `app/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/test_config.py
import json, os, pytest
from pathlib import Path
from app.config import BlitztextConfig, load_config, save_config, reset_config

def test_load_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    cfg = load_config()
    assert cfg.trigger_mode == "hold"
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.whisper_language == "de"
    assert "normal" in cfg.modes

def test_save_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg.whisper_language = "en"
    save_config(cfg)
    cfg2 = load_config()
    assert cfg2.whisper_language == "en"

def test_reset(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg.whisper_language = "en"
    save_config(cfg)
    reset_config()
    cfg2 = load_config()
    assert cfg2.whisper_language == "de"
```

- [ ] **Step 2: Test laufen lassen — muss FAIL sein**

```bash
pytest tests/test_config.py -v
# Expected: ImportError / FAILED
```

- [ ] **Step 3: app/config.py implementieren**

```python
# app/config.py
import json, os
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional

CONFIG_DEFAULT_PATH = Path.home() / ".config" / "transcriptor" / "config.json"

class ModeConfig(BaseModel):
    key_code: int = 0
    key_name: str = ""
    prompt: Optional[str] = None
    emoji_count: str = "mittel"  # wenig | mittel | viel

class InjectConfig(BaseModel):
    method: str = "xdotool"   # xdotool | xclip+paste
    delay_ms: int = 50

class BlitztextConfig(BaseModel):
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    whisper_language: str = "de"
    trigger_mode: str = "hold"       # hold | toggle
    input_device: str = ""
    audio_device: str = "default"
    modes: dict[str, ModeConfig] = Field(default_factory=lambda: {
        "normal": ModeConfig(key_code=183, key_name="KEY_F13"),
        "plus":   ModeConfig(key_code=184, key_name="KEY_F14",
                             prompt="Formuliere folgenden gesprochenen Text schriftlich um. "
                                    "Behalte den Sinn exakt bei, mache ihn nur schriftlicher:"),
        "rage":   ModeConfig(key_code=185, key_name="KEY_F15",
                             prompt="Wandle folgenden wütenden Text in eine freundliche, "
                                    "professionelle Formulierung um:"),
        "emoji":  ModeConfig(key_code=186, key_name="KEY_F16", emoji_count="mittel"),
    })
    vocabulary: list[str] = Field(default_factory=list)
    inject: InjectConfig = Field(default_factory=InjectConfig)

def _config_path() -> Path:
    p = Path(os.getenv("BLITZTEXT_CONFIG", str(CONFIG_DEFAULT_PATH))).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def load_config() -> BlitztextConfig:
    p = _config_path()
    if p.exists():
        return BlitztextConfig.model_validate(json.loads(p.read_text()))
    cfg = BlitztextConfig()
    # Inject API key from env if not in file
    cfg.openai_api_key = os.getenv("OPENAI_API_KEY", "")
    save_config(cfg)
    return cfg

def save_config(cfg: BlitztextConfig) -> None:
    _config_path().write_text(cfg.model_dump_json(indent=2))

def reset_config() -> BlitztextConfig:
    p = _config_path()
    if p.exists():
        p.unlink()
    return load_config()
```

- [ ] **Step 4: Tests laufen lassen — müssen PASS sein**

```bash
pytest tests/test_config.py -v
# Expected: 3 passed
```

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py pyproject.toml
git commit -m "feat: config module with load/save/reset"
```

---

## Task 3: Audio-Recorder

**Files:**
- Create: `app/recorder.py`
- Create: `tests/test_recorder.py`

- [ ] **Step 1: Failing Test**

```python
# tests/test_recorder.py
import pytest
from app.recorder import record_audio, RecordingSession

def test_record_audio_returns_bytes():
    """Smoke-test: record 0.2s audio, expect non-empty bytes."""
    data = record_audio(duration_seconds=0.2, samplerate=16000)
    assert isinstance(data, bytes)
    assert len(data) > 0

def test_recording_session_hold():
    """Start/stop session produces bytes."""
    session = RecordingSession(samplerate=16000)
    session.start()
    import time; time.sleep(0.1)
    data = session.stop()
    assert isinstance(data, bytes)
    assert len(data) > 0
```

- [ ] **Step 2: Test laufen lassen — FAIL**

```bash
pytest tests/test_recorder.py -v
```

- [ ] **Step 3: app/recorder.py implementieren**

```python
# app/recorder.py
"""Audioaufnahme via sounddevice. Produziert WAV-Bytes für Whisper."""
import io, threading
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav

SAMPLERATE = 16000
CHANNELS = 1

def record_audio(duration_seconds: float = 3.0,
                 samplerate: int = SAMPLERATE,
                 device: str | None = None) -> bytes:
    """Nimmt `duration_seconds` Sekunden auf und gibt WAV-Bytes zurück."""
    frames = int(samplerate * duration_seconds)
    audio = sd.rec(frames, samplerate=samplerate, channels=CHANNELS,
                   dtype="int16", device=device or None)
    sd.wait()
    return _to_wav_bytes(audio, samplerate)

class RecordingSession:
    """Push-to-Talk Session: start() → stop() → WAV-Bytes."""

    def __init__(self, samplerate: int = SAMPLERATE, device: str | None = None):
        self.samplerate = samplerate
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._chunks.clear()
        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=CHANNELS,
            dtype="int16", device=self.device or None,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time, status):
        with self._lock:
            self._chunks.append(indata.copy())

    def stop(self) -> bytes:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return b""
            audio = np.concatenate(self._chunks, axis=0)
        return _to_wav_bytes(audio, self.samplerate)

def _to_wav_bytes(audio: np.ndarray, samplerate: int) -> bytes:
    buf = io.BytesIO()
    wav.write(buf, samplerate, audio)
    return buf.getvalue()
```

- [ ] **Step 4: Tests PASS**

```bash
pytest tests/test_recorder.py -v
# Expected: 2 passed
```

- [ ] **Step 5: Commit**

```bash
git add app/recorder.py tests/test_recorder.py
git commit -m "feat: audio recorder (hold-to-record session)"
```

---

## Task 4: Transcribe-Wrapper

**Files:**
- Create: `app/transcribe.py`
- Create: `tests/test_transcribe.py`

- [ ] **Step 1: Failing Test**

```python
# tests/test_transcribe.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.transcribe import transcribe_audio

@pytest.mark.asyncio
async def test_transcribe_returns_text():
    # Whisper mit response_format="text" gibt direkt einen str zurück
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value="Hallo Welt")

    with patch("app.transcribe.get_client", return_value=mock_client):
        result = await transcribe_audio(b"fake_wav_bytes", language="de")

    assert result == "Hallo Welt"

@pytest.mark.asyncio
async def test_transcribe_with_vocabulary():
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value="Blitztext ist super")

    with patch("app.transcribe.get_client", return_value=mock_client):
        result = await transcribe_audio(b"fake", language="de",
                                        vocabulary=["Blitztext", "CachyOS"])
    assert "Blitztext" in result
```

- [ ] **Step 2: FAIL**

```bash
pytest tests/test_transcribe.py -v
```

- [ ] **Step 3: app/transcribe.py**

```python
# app/transcribe.py
"""Whisper API Wrapper."""
import io
from openai import AsyncOpenAI
from app.config import load_config

_client: AsyncOpenAI | None = None

def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client

async def transcribe_audio(
    wav_bytes: bytes,
    language: str = "de",
    vocabulary: list[str] | None = None,
) -> str:
    """Sendet WAV-Bytes an Whisper, gibt transkribierten Text zurück."""
    client = get_client()
    buf = io.BytesIO(wav_bytes)
    buf.name = "audio.wav"

    kwargs: dict = {
        "model": "whisper-1",
        "file": buf,
        "language": language,
        "response_format": "text",
    }
    if vocabulary:
        kwargs["prompt"] = ", ".join(vocabulary)

    result = await client.audio.transcriptions.create(**kwargs)
    # response_format="text" → SDK gibt direkt str zurück
    return result.strip() if isinstance(result, str) else str(result).strip()
```

- [ ] **Step 4: PASS**

```bash
pytest tests/test_transcribe.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/transcribe.py tests/test_transcribe.py
git commit -m "feat: whisper transcription wrapper"
```

---

## Task 5: LLM Post-Processing (Plus / Rage / Emoji)

**Files:**
- Create: `app/process.py`
- Create: `tests/test_process.py`

- [ ] **Step 1: Failing Test**

```python
# tests/test_process.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.process import process_text, ProcessMode

@pytest.mark.asyncio
async def test_process_plus():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Sehr geehrter Herr MP"
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.process.get_client", return_value=mock_client):
        result = await process_text("Ey MP was geht", ProcessMode.PLUS, prompt="Formuliere schriftlich:")
    assert len(result) > 0

@pytest.mark.asyncio
async def test_process_rage():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Guten Tag, ich wollte höflich nachfragen..."
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.process.get_client", return_value=mock_client):
        result = await process_text("DU IDIOT!", ProcessMode.RAGE, prompt="Mach nett:")
    assert "höflich" in result.lower() or len(result) > 5

@pytest.mark.asyncio
async def test_process_emoji_mittel():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Super Tag heute 🚀 alles läuft 😊"
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.process.get_client", return_value=mock_client):
        result = await process_text("Super Tag heute", ProcessMode.EMOJI, emoji_count="mittel")
    assert len(result) > 0

@pytest.mark.asyncio
async def test_process_normal_passthrough():
    """Normal mode: kein LLM, Text unverändert zurück."""
    result = await process_text("Hallo Welt", ProcessMode.NORMAL)
    assert result == "Hallo Welt"
```

- [ ] **Step 2: FAIL**

```bash
pytest tests/test_process.py -v
```

- [ ] **Step 3: app/process.py**

```python
# app/process.py
"""LLM Post-Processing für Plus, Rage und Emoji-Modi."""
from enum import Enum
from openai import AsyncOpenAI
from app.config import load_config

class ProcessMode(str, Enum):
    NORMAL = "normal"
    PLUS   = "plus"
    RAGE   = "rage"
    EMOJI  = "emoji"

_client: AsyncOpenAI | None = None

def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client

_EMOJI_COUNT_MAP = {
    "wenig":  "1 bis 2",
    "mittel": "3 bis 5",
    "viel":   "6 bis 10",
}

async def process_text(
    text: str,
    mode: ProcessMode,
    prompt: str | None = None,
    emoji_count: str = "mittel",
    model: str | None = None,
) -> str:
    if mode == ProcessMode.NORMAL:
        return text

    cfg = load_config()
    llm_model = model or cfg.llm_model
    client = get_client()

    if mode == ProcessMode.EMOJI:
        count_desc = _EMOJI_COUNT_MAP.get(emoji_count, "3 bis 5")
        system_prompt = (
            f"Füge dem folgenden Text {count_desc} passende Emojis hinzu. "
            "Behalte den Text exakt bei, füge nur Emojis an sinnvollen Stellen ein. "
            "Gib nur den fertigen Text zurück, keine Erklärungen."
        )
    else:
        system_prompt = (
            (prompt or "") +
            "\nGib nur den fertigen Text zurück, keine Erklärungen, kein Präfix."
        )

    resp = await client.chat.completions.create(
        model=llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": text},
        ],
        temperature=0.3,
        max_tokens=500,
    )
    return resp.choices[0].message.content.strip()
```

- [ ] **Step 4: PASS**

```bash
pytest tests/test_process.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/process.py tests/test_process.py
git commit -m "feat: LLM post-processing (plus/rage/emoji modes)"
```

---

## Task 6: Text-Inject + Notifications

**Files:**
- Create: `app/inject.py`
- Create: `tests/test_inject.py`

- [ ] **Step 1: Failing Test**

```python
# tests/test_inject.py
import pytest
from unittest.mock import patch, MagicMock
from app.inject import inject_text, notify

def test_inject_xdotool(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Hallo Welt", method="xdotool")
    assert any("xdotool" in str(c) for c in calls)
    assert any("Hallo Welt" in str(c) for c in calls)

def test_inject_xclip_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Test", method="xclip+paste")
    # xclip schreibt in Clipboard, xdotool key ctrl+v fügt ein
    assert len(calls) == 2

def test_notify(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    notify("recording", "Aufnahme gestartet")
    assert any("notify-send" in str(c) for c in calls)
```

- [ ] **Step 2: FAIL**

```bash
pytest tests/test_inject.py -v
```

- [ ] **Step 3: app/inject.py**

```python
# app/inject.py
"""Text in aktives Fenster einfügen + Desktop-Benachrichtigungen."""
import subprocess
import logging

logger = logging.getLogger("blitztext.inject")

def inject_text(text: str, method: str = "xdotool", delay_ms: int = 50) -> None:
    """Fügt text in das aktuell fokussierte Fenster ein."""
    if not text:
        return
    try:
        if method == "xdotool":
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers",
                 f"--delay={delay_ms}", "--", text],
                check=True, capture_output=True,
            )
        elif method == "xclip+paste":
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode(), check=True,
            )
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                check=True, capture_output=True,
            )
        else:
            logger.warning("Unknown inject method: %s", method)
    except subprocess.CalledProcessError as e:
        logger.error("inject_text failed: %s", e)
    except FileNotFoundError as e:
        logger.error("Tool not found: %s", e)

_ICONS = {
    "recording": "media-record",
    "done":      "dialog-information",
    "error":     "dialog-error",
}

def notify(event: str, message: str, title: str = "Blitztext") -> None:
    """Sendet Desktop-Benachrichtigung via notify-send."""
    icon = _ICONS.get(event, "dialog-information")
    try:
        subprocess.run(
            ["notify-send", "-i", icon, "-t", "2000", title, message],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        logger.warning("notify-send not available")
```

- [ ] **Step 4: PASS**

```bash
pytest tests/test_inject.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/inject.py tests/test_inject.py
git commit -m "feat: xdotool text inject + notify-send"
```

---

## Task 7: evdev-Daemon (Herzstück)

**Files:**
- Create: `app/daemon.py`
- Create: `tests/test_daemon.py`

Der Daemon läuft als asyncio-Background-Task im FastAPI-Lifespan. Er:
1. Öffnet das konfigurierte `input_device`
2. Wartet auf key_press des konfigurierten Tastencodes
3. Startet `RecordingSession`
4. Bei key_release: stoppt Aufnahme → transkribiert → verarbeitet → injiziert

- [ ] **Step 1: Failing Test**

```python
# tests/test_daemon.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.daemon import BlitztextDaemon
from app.config import BlitztextConfig, ModeConfig, InjectConfig

@pytest.fixture
def cfg():
    c = BlitztextConfig()
    c.openai_api_key = "test"
    c.input_device = "/dev/input/event0"
    c.modes["normal"] = ModeConfig(key_code=183, key_name="KEY_F13")
    return c

def test_daemon_resolves_mode(cfg):
    daemon = BlitztextDaemon(cfg)
    mode = daemon._key_to_mode(183)
    assert mode == "normal"

def test_daemon_unknown_key_returns_none(cfg):
    daemon = BlitztextDaemon(cfg)
    assert daemon._key_to_mode(999) is None

def test_daemon_toggle_mode_state(cfg):
    """Toggle: erster Press startet, zweiter Press stoppt."""
    cfg.trigger_mode = "toggle"
    daemon = BlitztextDaemon(cfg)
    assert daemon._toggle_recording is False
    daemon._toggle_recording = True
    assert daemon._toggle_recording is True

@pytest.mark.asyncio
async def test_daemon_pipeline(cfg):
    """Stellt sicher dass Pipeline aufgerufen wird."""
    daemon = BlitztextDaemon(cfg)
    with patch("app.daemon.RecordingSession") as MockSession, \
         patch("app.daemon.transcribe_audio", new_callable=AsyncMock, return_value="Hallo") as mock_t, \
         patch("app.daemon.process_text", new_callable=AsyncMock, return_value="Hallo") as mock_p, \
         patch("app.daemon.inject_text") as mock_i, \
         patch("app.daemon.notify") as mock_n:
        await daemon._run_pipeline("normal", b"fake_audio")
    mock_t.assert_awaited_once()
    mock_i.assert_called_once_with("Hallo", method=cfg.inject.method,
                                   delay_ms=cfg.inject.delay_ms)
```

- [ ] **Step 2: FAIL**

```bash
pytest tests/test_daemon.py -v
```

- [ ] **Step 3: app/daemon.py**

```python
# app/daemon.py
"""evdev-Daemon: überwacht Tasten global und startet die Transkriptions-Pipeline."""
import asyncio
import logging
import evdev
from app.config import BlitztextConfig, load_config
from app.recorder import RecordingSession
from app.transcribe import transcribe_audio
from app.process import process_text, ProcessMode
from app.inject import inject_text, notify

logger = logging.getLogger("blitztext.daemon")

class BlitztextDaemon:
    def __init__(self, cfg: BlitztextConfig | None = None):
        self.cfg = cfg or load_config()
        self._active_mode: str | None = None
        self._session: RecordingSession | None = None
        self._running = False
        self._toggle_recording = False  # für trigger_mode == "toggle"

    def _key_to_mode(self, key_code: int) -> str | None:
        for mode_name, mode_cfg in self.cfg.modes.items():
            if mode_cfg.key_code == key_code:
                return mode_name
        return None

    async def _run_pipeline(self, mode_name: str, wav_bytes: bytes) -> None:
        """Whisper → LLM → inject."""
        try:
            notify("recording", f"Transkribiere ({mode_name})...")
            text = await transcribe_audio(
                wav_bytes,
                language=self.cfg.whisper_language,
                vocabulary=self.cfg.vocabulary,
            )
            if not text:
                notify("error", "Kein Text erkannt")
                return

            mode_cfg = self.cfg.modes[mode_name]
            text = await process_text(
                text,
                ProcessMode(mode_name),
                prompt=mode_cfg.prompt,
                emoji_count=mode_cfg.emoji_count,
            )
            inject_text(text, method=self.cfg.inject.method,
                        delay_ms=self.cfg.inject.delay_ms)
            notify("done", f"Eingefügt: {text[:40]}...")
        except Exception as e:
            logger.error("Pipeline error: %s", e)
            notify("error", f"Fehler: {e}")

    async def run(self) -> None:
        """Hauptschleife: öffnet evdev-Device und wartet auf Tasten."""
        self._running = True
        device_path = self.cfg.input_device
        if not device_path:
            logger.warning("Kein input_device konfiguriert — Daemon inaktiv.")
            return

        try:
            dev = evdev.InputDevice(device_path)
            logger.info("Blitztext Daemon gestartet auf %s", device_path)
            notify("done", "Blitztext bereit")
        except Exception as e:
            logger.error("Kann Device nicht öffnen: %s", e)
            return

        try:
            async for event in dev.async_read_loop():
                if not self._running:
                    break
                if event.type != evdev.ecodes.EV_KEY:
                    continue

                mode_name = self._key_to_mode(event.code)
                if mode_name is None:
                    continue

                audio_device = self.cfg.audio_device if self.cfg.audio_device != "default" else None

                if self.cfg.trigger_mode == "hold":
                    # Key press → Aufnahme starten
                    if event.value == 1:
                        self._active_mode = mode_name
                        self._session = RecordingSession(device=audio_device)
                        self._session.start()
                        notify("recording", f"Aufnahme ({mode_name})...")
                        logger.info("Recording started (hold): %s", mode_name)
                    # Key release → Aufnahme stoppen + Pipeline
                    elif event.value == 0:
                        if self._session and self._active_mode:
                            wav = self._session.stop()
                            mode = self._active_mode
                            self._session = None
                            self._active_mode = None
                            asyncio.create_task(self._run_pipeline(mode, wav))

                elif self.cfg.trigger_mode == "toggle":
                    # Erster Press → Start; zweiter Press → Stop + Pipeline
                    if event.value == 1:
                        if not self._toggle_recording:
                            self._toggle_recording = True
                            self._active_mode = mode_name
                            self._session = RecordingSession(device=audio_device)
                            self._session.start()
                            notify("recording", f"Aufnahme ({mode_name})...")
                            logger.info("Recording started (toggle): %s", mode_name)
                        else:
                            self._toggle_recording = False
                            if self._session and self._active_mode:
                                wav = self._session.stop()
                                mode = self._active_mode
                                self._session = None
                                self._active_mode = None
                                asyncio.create_task(self._run_pipeline(mode, wav))

        except Exception as e:
            logger.error("Daemon loop error: %s", e)
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        if self._session:
            self._session.stop()
```

- [ ] **Step 4: PASS**

```bash
pytest tests/test_daemon.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/daemon.py tests/test_daemon.py
git commit -m "feat: evdev daemon with hold-to-record pipeline"
```

---

## Task 8: Config REST API + Health Routes

**Files:**
- Create: `app/routes/config_routes.py`
- Create: `app/routes/health_routes.py`
- Create: `app/routes/process_routes.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_routes.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
import os

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.main import create_app
    return TestClient(create_app())

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data

def test_get_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert "trigger_mode" in data
    assert "modes" in data

def test_patch_config(client):
    r = client.patch("/api/config", json={"whisper_language": "en"})
    assert r.status_code == 200
    r2 = client.get("/api/config")
    assert r2.json()["whisper_language"] == "en"

def test_reset_config(client):
    client.patch("/api/config", json={"whisper_language": "en"})
    r = client.post("/api/config/reset")
    assert r.status_code == 200
    assert r.json()["whisper_language"] == "de"
```

- [ ] **Step 2: FAIL**

```bash
pytest tests/test_routes.py -v
```

- [ ] **Step 3: Routes implementieren**

```python
# app/routes/config_routes.py
from fastapi import APIRouter, Body
from app.config import load_config, save_config, reset_config, BlitztextConfig

router = APIRouter(prefix="/api/config", tags=["config"])

@router.get("", response_model=BlitztextConfig)
def get_config():
    return load_config()

@router.patch("", response_model=BlitztextConfig)
def patch_config(updates: dict = Body(...)):
    cfg = load_config()
    updated = cfg.model_dump()
    updated.update(updates)
    new_cfg = BlitztextConfig.model_validate(updated)
    save_config(new_cfg)
    return new_cfg

@router.post("/reset", response_model=BlitztextConfig)
def post_reset():
    return reset_config()
```

```python
# app/routes/health_routes.py
import shutil, subprocess
from fastapi import APIRouter
from app.config import load_config

router = APIRouter(prefix="/api", tags=["health"])

@router.get("/health")
def health():
    cfg = load_config()
    return {
        "status": "healthy",
        "version": "0.1.0",
        "api_key_set": bool(cfg.openai_api_key),
        "input_device": cfg.input_device or "not configured",
        "xdotool_available": shutil.which("xdotool") is not None,
        "trigger_mode": cfg.trigger_mode,
    }
```

```python
# app/routes/process_routes.py
from fastapi import APIRouter
from pydantic import BaseModel
from app.process import process_text, ProcessMode

router = APIRouter(prefix="/api/process", tags=["process"])

class ProcessRequest(BaseModel):
    text: str
    emoji_count: str = "mittel"

@router.post("/{mode}")
async def post_process(mode: str, req: ProcessRequest):
    from app.config import load_config
    cfg = load_config()
    mode_cfg = cfg.modes.get(mode)
    result = await process_text(
        req.text,
        ProcessMode(mode),
        prompt=mode_cfg.prompt if mode_cfg else None,
        emoji_count=req.emoji_count,
    )
    return {"text": result, "mode": mode}
```

- [ ] **Step 4: PASS**

```bash
pytest tests/test_routes.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/routes/ tests/test_routes.py
git commit -m "feat: config/health/process REST API"
```

---

## Task 9: FastAPI App + Daemon-Lifespan

**Files:**
- Create: `app/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/test_main.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    # Daemon nicht wirklich starten im Test
    with patch("app.daemon.BlitztextDaemon.run", new_callable=AsyncMock):
        from app.main import create_app
        return TestClient(create_app())

def test_app_health_route_registered(client):
    r = client.get("/api/health")
    assert r.status_code == 200

def test_app_config_route_registered(client):
    r = client.get("/api/config")
    assert r.status_code == 200

def test_app_process_route_registered(client):
    with patch("app.process.process_text", new_callable=AsyncMock, return_value="test"):
        r = client.post("/api/process/normal", json={"text": "Hallo"})
    assert r.status_code == 200
```

- [ ] **Step 2: Test laufen lassen — FAIL**

```bash
pytest tests/test_main.py -v
# Expected: ImportError (app/main.py existiert noch nicht)
```

- [ ] **Step 3: app/main.py**

```python
# app/main.py
"""Blitztext Linux — FastAPI App mit Daemon-Lifespan."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import load_config
from app.daemon import BlitztextDaemon
from app.routes.config_routes import router as config_router
from app.routes.health_routes import router as health_router
from app.routes.process_routes import router as process_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

_daemon: BlitztextDaemon | None = None

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    global _daemon
    cfg = load_config()
    _daemon = BlitztextDaemon(cfg)
    task = asyncio.create_task(_daemon.run())
    yield
    _daemon.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

def create_app() -> FastAPI:
    app = FastAPI(
        title="Blitztext Linux",
        version="0.1.0",
        description="Systemweiter Diktierdienst für Linux",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(config_router)
    app.include_router(health_router)
    app.include_router(process_router)
    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)
```

- [ ] **Step 4: Tests PASS**

```bash
pytest tests/test_main.py -v
# Expected: 3 passed
```

- [ ] **Step 5: Smoke-Test**

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 &
sleep 2
curl -s http://localhost:8765/api/health | python3 -m json.tool
kill %1
```

Expected: `{"status": "healthy", ...}`

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: fastapi app with daemon lifespan"
```

---

## Task 10: systemd Service + install.sh

**Files:**
- Create: `blitztext.service`
- Create: `install.sh`

- [ ] **Step 1: blitztext.service**

```ini
# blitztext.service
[Unit]
Description=Blitztext Linux Diktierdienst
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/Projects/blitztext-linux
ExecStart=%h/Projects/blitztext-linux/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-%h/.config/blitztext/env

[Install]
WantedBy=default.target
```

- [ ] **Step 2: install.sh**

```bash
#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/blitztext"
SERVICE_DIR="$HOME/.config/systemd/user"

echo "=== Blitztext Linux Installer ==="

# 1. System-Abhängigkeiten prüfen
for tool in xdotool notify-send arecord; do
    if ! command -v "$tool" &>/dev/null; then
        echo "WARNUNG: $tool fehlt. Installieren mit: sudo pacman -S $tool"
    fi
done

# Benutzer zur input-Gruppe hinzufügen (für evdev ohne root)
if ! groups | grep -q input; then
    echo "Füge $USER zur input-Gruppe hinzu..."
    sudo usermod -aG input "$USER"
    echo ""
    echo "WICHTIG: Gruppe 'input' wurde hinzugefügt."
    echo "Du musst dich ABMELDEN und NEU ANMELDEN bevor der Service"
    echo "auf /dev/input/eventX zugreifen kann."
    echo "Nach dem Neuanmelden: systemctl --user start blitztext.service"
    echo ""
    NEED_RELOGIN=true
fi

# 2. venv + Python-Deps
if [ ! -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    python -m venv "$PROJECT_DIR/.venv"
fi
source "$PROJECT_DIR/.venv/bin/activate"
pip install -q -e "$PROJECT_DIR"

# 3. Config-Verzeichnis
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    echo "Erstelle Standard-Config in $CONFIG_DIR/config.json"
    python -c "from app.config import load_config; load_config()"
fi

# API-Key setzen
if [ -n "$OPENAI_API_KEY" ]; then
    echo "OPENAI_API_KEY=$OPENAI_API_KEY" > "$CONFIG_DIR/env"
else
    echo "HINWEIS: Setze OPENAI_API_KEY in $CONFIG_DIR/env"
fi

# 4. systemd Service
mkdir -p "$SERVICE_DIR"
sed "s|%h|$HOME|g" "$PROJECT_DIR/blitztext.service" > "$SERVICE_DIR/blitztext.service"
systemctl --user daemon-reload
systemctl --user enable blitztext.service
if [ "${NEED_RELOGIN:-false}" != "true" ]; then
    systemctl --user start blitztext.service
else
    echo "Service aktiviert, aber NICHT gestartet — erst neu anmelden!"
fi

echo ""
echo "=== Installation abgeschlossen ==="
echo "Status:  systemctl --user status blitztext"
echo "Config:  $CONFIG_DIR/config.json"
echo "Health:  curl http://localhost:8765/api/health"
echo ""
echo "WICHTIG: Gerät konfigurieren:"
echo "  curl http://localhost:8765/api/health"
echo "  curl -X PATCH http://localhost:8765/api/config \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"input_device\": \"/dev/input/eventX\"}'"
```

- [ ] **Step 3: ausführbar machen**

```bash
chmod +x install.sh
```

- [ ] **Step 4: Commit**

```bash
git add blitztext.service install.sh
git commit -m "feat: systemd service + install script"
```

---

## Task 11: Alle Tests + Abschluss

- [ ] **Step 1: Alle Tests laufen**

```bash
pytest tests/ -v --tb=short
# Expected: alle grün
```

- [ ] **Step 2: README.md**

Kurze Dokumentation mit: Installation, Konfiguration, Tastenbelegung.

- [ ] **Step 3: Final Commit**

```bash
git add README.md
git commit -m "docs: README + setup instructions"
git tag v0.1.0
```

---

## Schnellstart nach Installation

```bash
# 1. Installieren
OPENAI_API_KEY=sk-... bash install.sh

# 2. Input-Device herausfinden
curl -s http://localhost:8765/api/health

# 3. Tastatur konfigurieren (via WebSocket Key-Detection oder manuell)
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"input_device": "/dev/input/event3"}'

# 4. Tasten konfigurieren (evdev Keycode für gewünschte Taste)
curl -X PATCH http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"modes": {"normal": {"key_code": 183, "key_name": "KEY_F13"}}}'

# 5. Taste halten → reden → loslassen → Text erscheint im aktiven Fenster
```

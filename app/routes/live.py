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
_pump_tasks: set[asyncio.Task] = set()
_cached_config = None


def _get_config():
    global _cached_config
    if _cached_config is None:
        from app.config import load_config
        _cached_config = load_config()
    return _cached_config

_LANG_NORMALIZE = {
    "german": "de",
    "english": "en",
    "cebuano": "ceb",
    "bisaya": "ceb",
}

_STATIC_DIR = Path(__file__).parent.parent / "static"


# ---------------------------------------------------------------------------
# Public API (fuer daemon.py)
# ---------------------------------------------------------------------------

def set_sessions(
    mic: LiveRecordingSession | None,
    desktop: LiveRecordingSession | None,
) -> None:
    """Daemon setzt aktive Sessions. Nur Speicherung — keine Tasks starten.
    Daemon ruft anschliessend asyncio.create_task(live.start_pumps()) auf.
    """
    global _live_mic_session, _live_desktop_session
    _live_mic_session = mic
    _live_desktop_session = desktop


async def start_pumps() -> None:
    """Startet Pump-Tasks. Muss aus async-Kontext aufgerufen werden (via create_task)."""
    # Cancel any existing pump tasks before starting new ones
    for task in list(_pump_tasks):
        task.cancel()
    _pump_tasks.clear()

    if _live_mic_session:
        t = asyncio.create_task(_pump_session(_live_mic_session, _mic_subscribers))
        _pump_tasks.add(t)
        t.add_done_callback(_pump_tasks.discard)
    if _live_desktop_session:
        t = asyncio.create_task(_pump_session(_live_desktop_session, _desktop_subscribers))
        _pump_tasks.add(t)
        t.add_done_callback(_pump_tasks.discard)


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
    """Transkribiert WAV-Bytes und gibt (text, lang_detected) zurueck.

    Verwendet verbose_json fuer Sprachdetektierung (Online-Pfad).
    Lokaler Pfad: faster-whisper info.language.
    """
    cfg = _get_config()

    if cfg.transcribe_backend == "local":
        try:
            from app.transcribe import _fw_engine
            loop = asyncio.get_running_loop()

            def _run():
                import tempfile
                import os
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
        async with asyncio.TaskGroup() as tg:
            tg.create_task(handle_client())
            tg.create_task(stream())
    except* (WebSocketDisconnect, Exception):
        pass
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
        async with asyncio.TaskGroup() as tg:
            tg.create_task(handle_client())
            tg.create_task(stream())
    except* (WebSocketDisconnect, Exception):
        pass
    finally:
        _desktop_subscribers.discard(my_queue)

# app/routes/live.py
"""Live-Transkription WebSocket-Endpoints + HTML-Seite."""
import asyncio
import io
import logging
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app import meeting_log
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
_stop_callback: Callable[[], None] | None = None

# Zielsprache gilt pro Kanal, nicht pro Browser-Tab: transkribiert und uebersetzt
# wird genau einmal pro Session, damit die Mitschrift eindeutig ist.
_target_lang: dict[str, str] = {"mic": "de", "desktop": "de"}

# Gesprochene Sprache pro Kanal. "" = Auto-Erkennung.
# Wichtig: Auto-Erkennung laeuft bei Whisper PRO 4-Sekunden-Chunk neu und kippt
# an kurzen/undeutlichen Stellen auf Englisch — mitten im deutschen Meeting.
# Deshalb ist eine feste Sprache der Standard.
_source_lang: dict[str, str] = {"mic": "", "desktop": ""}
_desktop_detector: "_SpeakerDetector | None" = None


def _effective_source_lang(channel: str) -> str | None:
    """Sprache fuer Whisper. None = Auto-Erkennung (nur wenn ausdruecklich gewaehlt)."""
    lang = _source_lang.get(channel)
    if lang == "auto":
        return None
    if lang:
        return lang
    return _get_config().whisper_language or None


def _get_config():
    global _cached_config
    if _cached_config is None:
        from app.config import load_config
        _cached_config = load_config()
    return _cached_config


def invalidate_config_cache() -> None:
    """Naechster Zugriff laedt die Config neu (nach Aenderung im Tray)."""
    global _cached_config
    _cached_config = None

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

    Startet bzw. schliesst hier zentral die Meeting-Mitschrift, damit kein
    Aufrufer das Speichern vergessen kann.
    """
    global _live_mic_session, _live_desktop_session, _desktop_detector
    _live_mic_session = mic
    _live_desktop_session = desktop
    if mic is not None or desktop is not None:
        _desktop_detector = _SpeakerDetector()
        meeting_log.start_session()
    else:
        _desktop_detector = None
        meeting_log.close_session()


def set_stop_callback(fn: Callable[[], None] | None) -> None:
    """Daemon registriert einen Callback der bei Browser-Stop aufgerufen wird."""
    global _stop_callback
    _stop_callback = fn


async def start_pumps() -> None:
    """Startet Pump-Tasks. Muss aus async-Kontext aufgerufen werden (via create_task)."""
    # Cancel any existing pump tasks before starting new ones
    for task in list(_pump_tasks):
        task.cancel()
    _pump_tasks.clear()

    if _live_mic_session:
        t = asyncio.create_task(
            _pump_session(_live_mic_session, _mic_subscribers, "mic"))
        _pump_tasks.add(t)
        t.add_done_callback(_pump_tasks.discard)
    if _live_desktop_session:
        t = asyncio.create_task(
            _pump_session(_live_desktop_session, _desktop_subscribers, "desktop"))
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
    channel: str,
) -> None:
    """Transkribiert Chunks EINMAL pro Session, schreibt sie auf Platte und
    broadcastet das fertige Ergebnis an alle verbundenen Browser.

    Bewusst hier statt im WebSocket-Handler: die Mitschrift muss auch dann
    weiterlaufen, wenn kein Browser (mehr) verbunden ist, und mehrere Tabs
    duerfen dieselben 4 Sekunden Audio nicht mehrfach durch Whisper jagen.
    """
    while True:
        chunk = await session.queue.get()
        if chunk is None:
            await _broadcast(subscribers, None)
            break
        try:
            event = await _process_chunk(chunk, channel)
        except Exception as e:
            logger.error("%s transcribe error: %s", channel, e)
            continue
        if event is not None:
            await _broadcast(subscribers, event)


async def _process_chunk(wav_bytes: bytes, channel: str) -> dict | None:
    """Transkribiert, uebersetzt, persistiert. None = nichts Verwertbares."""
    text, lang_detected = await _transcribe_with_lang(wav_bytes, channel)
    if not text:
        return None

    target = _target_lang.get(channel, "")
    translation = None
    if target and target != lang_detected:
        try:
            candidate = await translate(text, target, source_lang=lang_detected)
            # Das LLM gibt bei gleicher Sprache haeufig den Originalsatz zurueck.
            # Eine "Uebersetzung", die dem Original entspricht, ist nur Rauschen.
            if candidate and candidate.strip() != text.strip():
                translation = candidate
        except Exception as e:
            logger.warning("%s translate failed: %s", channel, e)

    speaker = None
    if channel == "desktop" and _desktop_detector is not None:
        speaker = _desktop_detector.detect(wav_bytes)

    log = meeting_log.current()
    if log is not None:
        log.append(channel=channel, text=text, lang=lang_detected,
                   translation=translation, speaker=speaker)

    return {
        "text": text,
        "translation": translation,
        "speaker": speaker,
        "lang_detected": lang_detected,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }


async def _transcribe_with_lang(wav_bytes: bytes, channel: str = "mic") -> tuple[str, str]:
    """Transkribiert WAV-Bytes und gibt (text, lang_detected) zurueck.

    Die Sprache wird festgenagelt, sofern eine gewaehlt ist: Whisper erkennt sonst
    pro 4-Sekunden-Chunk neu und kippt an kurzen Stellen mitten im deutschen
    Meeting auf Englisch — inklusive englischer Halluzination des Gesagten.
    """
    cfg = _get_config()
    lang = _effective_source_lang(channel)

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
                    segments, info = _fw_engine._model.transcribe(
                        tmp_path,
                        language=lang,
                        # Without these, a 4s silence chunk costs ~35s of decoding
                        # (temperature fallback loop) and stalls the whole live queue.
                        condition_on_previous_text=False,
                        no_speech_threshold=0.6,
                        vad_filter=True,
                    )
                    if info.duration_after_vad < 0.3:
                        return "", ""  # silence — no text, no bogus language
                    text = " ".join(seg.text.strip() for seg in segments).strip()
                    # Bei fester Sprache zaehlt die Vorgabe, nicht Whispers Rateergebnis.
                    return text, lang or _normalize_lang(info.language or "")
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
    kwargs: dict = {"model": "whisper-1", "file": buf, "response_format": "verbose_json"}
    if lang:
        kwargs["language"] = lang
    result = await client.audio.transcriptions.create(**kwargs)
    detected = lang or _normalize_lang(getattr(result, "language", "") or "")
    text = getattr(result, "text", "") or ""
    return text.strip(), detected


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


@router.post("/live/start")
async def start_live_route() -> dict:
    """Startet den Live-Modus (idempotent) — für Tray-/UI-Steuerung."""
    from app.main import get_daemon
    daemon = get_daemon()
    if daemon is None:
        return {"ok": False, "error": "Daemon nicht bereit"}
    started = await daemon.start_live()
    return {"ok": True, "started": started}


@router.get("/live/history")
async def live_history() -> dict:
    """Laufende Mitschrift — damit ein Reload/Reconnect nichts verliert."""
    log = meeting_log.current()
    if log is None:
        return {"active": False, "session_id": None, "entries": []}
    return {"active": True, "session_id": log.session_id, "entries": log.entries()}


@router.get("/live/sessions")
async def live_sessions() -> dict:
    """Alle bisherigen Meetings, neueste zuerst."""
    return {"dir": str(meeting_log.base_dir()), "sessions": meeting_log.list_sessions()}


@router.get("/live/session/{session_id}/markdown")
async def live_session_markdown(session_id: str) -> Response:
    """Protokoll als Markdown-Download. 'current' = laufende Session."""
    log = meeting_log.current() if session_id == "current" else meeting_log.load_session(session_id)
    if log is None:
        return PlainTextResponse("Session nicht gefunden", status_code=404)
    return Response(
        content=log.to_markdown(),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="meeting_{log.session_id}.md"'},
    )


@router.post("/live/workshop/start")
async def workshop_start(model: str = "sonnet", watch_screen: bool = False) -> dict:
    """Startet den Workshop-Assistenten auf der laufenden Mitschrift.

    watch_screen erfasst den GESAMTEN Bildschirm — deshalb standardmäßig aus.
    """
    from app import workshop
    return workshop.start(model, watch_screen=watch_screen)


@router.post("/live/workshop/stop")
async def workshop_stop() -> dict:
    from app import workshop
    return await workshop.stop()


@router.get("/live/workshop/status")
async def workshop_status() -> dict:
    from app import workshop
    return workshop.status()


@router.get("/live/workshop/view")
async def workshop_view() -> Response:
    """Die gerenderte Artefakt-Ansicht."""
    from app import workshop
    agent = workshop.current()
    if agent is None:
        return PlainTextResponse("Workshop-Assistent läuft nicht", status_code=404)
    page = agent.out_dir / "uebersicht.html"
    if not page.exists():
        return PlainTextResponse(
            "Noch nichts verdichtet — der erste Durchgang braucht ein paar Minuten Gespräch.",
            status_code=202,
        )
    return Response(content=page.read_text(encoding="utf-8"), media_type="text/html")


@router.post("/live/stop")
async def stop_live() -> dict:
    """Stoppt beide Live-Sessions vom Browser aus."""
    mic = _live_mic_session
    desktop = _live_desktop_session
    # Erst den Workshop-Assistenten auslaufen lassen (er macht einen
    # Abschlussdurchgang), dann die Mitschrift schliessen — sonst fehlt ihm
    # beim letzten Lauf die Session.
    from app import workshop
    if workshop.current() is not None:
        await workshop.stop()
    set_sessions(None, None)
    # Pump-Tasks cancellen
    for task in list(_pump_tasks):
        task.cancel()
    _pump_tasks.clear()
    # Daemon-State zuruecksetzen
    if _stop_callback:
        _stop_callback()
    loop = asyncio.get_running_loop()
    if mic:
        loop.run_in_executor(None, mic.stop)
    if desktop:
        loop.run_in_executor(None, desktop.stop)
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket Routes
# ---------------------------------------------------------------------------

class _ClientGone:
    """Sentinel: der Browser ist weg — Sende-Schleife beenden."""


async def _serve_subscriber(
    ws: WebSocket,
    subscribers: set[asyncio.Queue],
    on_message: Callable[[dict], None],
) -> None:
    """Haengt einen Browser an einen Kanal und raeumt beim Trennen sicher auf.

    Der Sende-Task wartet blockierend auf der Queue. Ohne das _ClientGone-Signal
    aus handle_client() wuerde er ewig warten, die TaskGroup nie enden und die
    Queue nie aus `subscribers` verschwinden — bei einem Reconnect-Loop alle 3s
    haetten sich so ueber ein Meeting hinweg hunderte Karteileichen angesammelt.
    """
    my_queue: asyncio.Queue = asyncio.Queue()
    subscribers.add(my_queue)

    async def handle_client() -> None:
        try:
            async for msg in ws.iter_json():
                on_message(msg)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            my_queue.put_nowait(_ClientGone)

    async def stream() -> None:
        try:
            while True:
                event = await my_queue.get()
                if event is _ClientGone:
                    break
                if event is None:
                    await ws.send_json({"done": True})
                    break
                await ws.send_json(event)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(handle_client())
            tg.create_task(stream())
    except* (WebSocketDisconnect, Exception):
        pass
    finally:
        subscribers.discard(my_queue)

@router.websocket("/ws/live/mic")
async def ws_live_mic(ws: WebSocket) -> None:
    await ws.accept()
    if _live_mic_session is None:
        await ws.send_json({"error": "Keine aktive Live-Session"})
        await ws.close()
        return
    def on_message(msg: dict) -> None:
        if "set_target_lang" in msg:
            _target_lang["mic"] = msg["set_target_lang"]
        if "set_source_lang" in msg:
            # Greift ab dem naechsten Chunk — auch mitten im laufenden Transkript.
            _source_lang["mic"] = msg["set_source_lang"]
        if "mute" in msg and _live_mic_session:
            _live_mic_session.set_muted(bool(msg["mute"]))

    await _serve_subscriber(ws, _mic_subscribers, on_message)


@router.websocket("/ws/live/desktop")
async def ws_live_desktop(ws: WebSocket) -> None:
    await ws.accept()
    if _live_desktop_session is None:
        msg = "Kein Monitor-Gerät gefunden" if _live_mic_session else "Keine aktive Live-Session"
        await ws.send_json({"error": msg})
        await ws.close()
        return
    def on_message(msg: dict) -> None:
        if "set_target_lang" in msg:
            _target_lang["desktop"] = msg["set_target_lang"]
        if "set_source_lang" in msg:
            _source_lang["desktop"] = msg["set_source_lang"]

    await _serve_subscriber(ws, _desktop_subscribers, on_message)

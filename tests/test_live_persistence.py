"""Persistenz des Live-Modus: schreibt serverseitig, unabhaengig vom Browser."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import meeting_log
from app.routes import live


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_log, "_BASE_DIR", tmp_path / "meetings")
    meeting_log.close_session()
    live._mic_subscribers.clear()
    live._desktop_subscribers.clear()
    live._target_lang.update({"mic": "de", "desktop": "de"})
    live._source_lang.update({"mic": "", "desktop": ""})
    yield
    live.set_sessions(None, None)
    meeting_log.close_session()
    live._mic_subscribers.clear()
    live._desktop_subscribers.clear()


def test_set_sessions_starts_and_closes_meeting_log():
    """Mitschrift startet mit der Session — kein Aufrufer kann es vergessen."""
    assert meeting_log.current() is None
    live.set_sessions(MagicMock(), None)
    assert meeting_log.current() is not None
    live.set_sessions(None, None)
    assert meeting_log.current() is None


def test_process_chunk_persists_even_without_browser():
    """Kernanforderung: ohne verbundenen Browser wird trotzdem geschrieben."""
    live.set_sessions(MagicMock(), None)
    assert len(live._mic_subscribers) == 0  # niemand verbunden

    with patch.object(live, "_transcribe_with_lang",
                      AsyncMock(return_value=("Hallo aus dem Meeting", "de"))):
        event = asyncio.run(live._process_chunk(b"wav", "mic"))

    assert event["text"] == "Hallo aus dem Meeting"
    entries = meeting_log.current().entries()
    assert [e["text"] for e in entries] == ["Hallo aus dem Meeting"]


def test_process_chunk_skips_empty_text():
    live.set_sessions(MagicMock(), None)
    with patch.object(live, "_transcribe_with_lang", AsyncMock(return_value=("", ""))):
        event = asyncio.run(live._process_chunk(b"wav", "mic"))
    assert event is None
    assert meeting_log.current().entries() == []


def test_process_chunk_translates_and_persists_translation():
    live.set_sessions(MagicMock(), None)
    live._target_lang["desktop"] = "de"
    with patch.object(live, "_transcribe_with_lang",
                      AsyncMock(return_value=("Hello there", "en"))), \
         patch.object(live, "translate", AsyncMock(return_value="Hallo dort")):
        event = asyncio.run(live._process_chunk(b"wav", "desktop"))

    assert event["translation"] == "Hallo dort"
    entry = meeting_log.current().entries()[0]
    assert entry["translation"] == "Hallo dort"
    assert entry["channel"] == "desktop"


def test_process_chunk_survives_translation_failure():
    """Uebersetzung kaputt darf die Mitschrift nicht verhindern."""
    live.set_sessions(MagicMock(), None)
    with patch.object(live, "_transcribe_with_lang",
                      AsyncMock(return_value=("Hello", "en"))), \
         patch.object(live, "translate", AsyncMock(side_effect=RuntimeError("ollama weg"))):
        event = asyncio.run(live._process_chunk(b"wav", "desktop"))

    assert event["text"] == "Hello"
    assert event["translation"] is None
    assert meeting_log.current().entries()[0]["text"] == "Hello"


def test_pump_transcribes_once_for_two_subscribers():
    """Zwei offene Tabs duerfen dieselben 4s Audio nicht doppelt transkribieren."""
    live.set_sessions(MagicMock(), None)
    calls = []

    async def fake_transcribe(wav, channel="mic"):
        calls.append(wav)
        return "einmal", "de"

    async def _run():
        session = MagicMock()
        session.queue = asyncio.Queue()
        q1: asyncio.Queue = asyncio.Queue()
        q2: asyncio.Queue = asyncio.Queue()
        live._mic_subscribers.update({q1, q2})

        session.queue.put_nowait(b"audio")
        session.queue.put_nowait(None)
        with patch.object(live, "_transcribe_with_lang", fake_transcribe):
            await live._pump_session(session, live._mic_subscribers, "mic")

        return (await q1.get()), (await q2.get())

    e1, e2 = asyncio.run(_run())
    assert len(calls) == 1, "Audio wurde mehrfach transkribiert"
    assert e1["text"] == e2["text"] == "einmal"
    assert len(meeting_log.current().entries()) == 1


def test_fixed_source_lang_overrides_whisper_guess():
    """Whispers Sprach-Rateergebnis darf eine feste Sprachwahl nicht überstimmen."""
    live.set_sessions(MagicMock(), None)
    live._source_lang["mic"] = "de"

    seen = {}

    class FakeInfo:
        duration_after_vad = 3.0
        language = "en"  # Whisper rät Englisch

    def fake_transcribe(path, **kwargs):
        seen.update(kwargs)
        seg = MagicMock()
        seg.text = "das Social Credit System"
        return [seg], FakeInfo()

    cfg = MagicMock()
    cfg.transcribe_backend = "local"
    cfg.local_whisper_model = "small"
    cfg.whisper_language = "de"

    fake_engine = MagicMock()
    fake_engine._model.transcribe = fake_transcribe

    with patch.object(live, "_get_config", return_value=cfg), \
         patch("app.transcribe._fw_engine", fake_engine):
        text, lang = asyncio.run(live._transcribe_with_lang(b"wav", "mic"))

    assert seen["language"] == "de", "feste Sprache wurde nicht an Whisper gereicht"
    assert lang == "de", f"Whispers 'en'-Rateergebnis hat gewonnen (bekam {lang!r})"
    assert text == "das Social Credit System"


def test_source_lang_auto_lets_whisper_decide():
    """'Automatisch' muss die Erkennung weiterhin erlauben."""
    live.set_sessions(MagicMock(), None)
    live._source_lang["mic"] = "auto"

    seen = {}

    class FakeInfo:
        duration_after_vad = 3.0
        language = "en"

    def fake_transcribe(path, **kwargs):
        seen.update(kwargs)
        seg = MagicMock()
        seg.text = "hello there"
        return [seg], FakeInfo()

    cfg = MagicMock()
    cfg.transcribe_backend = "local"
    cfg.local_whisper_model = "small"
    cfg.whisper_language = "de"

    fake_engine = MagicMock()
    fake_engine._model.transcribe = fake_transcribe

    with patch.object(live, "_get_config", return_value=cfg), \
         patch("app.transcribe._fw_engine", fake_engine):
        text, lang = asyncio.run(live._transcribe_with_lang(b"wav", "mic"))

    assert seen["language"] is None
    assert lang == "en"


def test_echo_translation_is_discarded():
    """Eine 'Übersetzung', die dem Original entspricht, ist Rauschen — nicht anzeigen."""
    live.set_sessions(MagicMock(), None)
    live._target_lang["mic"] = "en"

    original = "to what's the social credit system and that's the right one."
    with patch.object(live, "_transcribe_with_lang",
                      AsyncMock(return_value=(original, "de"))), \
         patch.object(live, "translate", AsyncMock(return_value=original)):
        event = asyncio.run(live._process_chunk(b"wav", "mic"))

    assert event["translation"] is None
    assert meeting_log.current().entries()[0]["translation"] is None


def test_real_translation_is_kept():
    live.set_sessions(MagicMock(), None)
    live._target_lang["mic"] = "en"

    with patch.object(live, "_transcribe_with_lang",
                      AsyncMock(return_value=("Guten Morgen", "de"))), \
         patch.object(live, "translate", AsyncMock(return_value="Good morning")):
        event = asyncio.run(live._process_chunk(b"wav", "mic"))

    assert event["translation"] == "Good morning"


def _ws_only_app():
    """App nur mit den Live-Routen — ohne Lifespan, damit kein evdev-Daemon startet."""
    from fastapi import FastAPI
    from app.routes.live import router as live_router
    a = FastAPI()
    a.include_router(live_router)
    return a


@pytest.mark.parametrize("path,subs", [
    ("/ws/live/mic", "_mic_subscribers"),
    ("/ws/live/desktop", "_desktop_subscribers"),
])
def test_subscribers_are_removed_after_disconnect(path, subs):
    """Die Live-Seite verbindet bei Abbruch alle 3s neu — es darf sich nichts ansammeln."""
    import time
    from fastapi.testclient import TestClient

    live.set_sessions(MagicMock(), MagicMock())
    subscribers = getattr(live, subs)

    with TestClient(_ws_only_app()) as client:
        for _ in range(10):
            with client.websocket_connect(path) as ws:
                ws.send_json({"set_target_lang": ""})
        for _ in range(40):  # Aufraeumen braucht einen Event-Loop-Tick
            if not subscribers:
                break
            time.sleep(0.05)

    assert len(subscribers) == 0, f"{len(subscribers)} Karteileichen nach 10 Reconnects"


def test_pump_keeps_running_after_transcribe_error():
    """Ein kaputter Chunk darf das Meeting nicht beenden."""
    live.set_sessions(MagicMock(), None)
    seen = []

    async def flaky(wav, channel="mic"):
        if wav == b"bad":
            raise RuntimeError("whisper kaputt")
        seen.append(wav)
        return "danach", "de"

    async def _run():
        session = MagicMock()
        session.queue = asyncio.Queue()
        for item in (b"bad", b"good", None):
            session.queue.put_nowait(item)
        with patch.object(live, "_transcribe_with_lang", flaky):
            await live._pump_session(session, live._mic_subscribers, "mic")

    asyncio.run(_run())
    assert seen == [b"good"]
    assert [e["text"] for e in meeting_log.current().entries()] == ["danach"]

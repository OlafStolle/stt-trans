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

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

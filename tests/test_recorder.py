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

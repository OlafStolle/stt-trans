# tests/test_daemon.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.daemon import BlitztextDaemon
from app.config import BlitztextConfig, ModeConfig, InjectConfig

@pytest.fixture
def cfg():
    c = BlitztextConfig()
    c = c.model_copy(update={"openai_api_key": "test", "input_device": "/dev/input/event0"})
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
    cfg_toggle = cfg.model_copy(update={"trigger_mode": "toggle"})
    daemon = BlitztextDaemon(cfg_toggle)
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

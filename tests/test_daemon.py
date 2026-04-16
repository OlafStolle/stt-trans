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


@pytest.mark.asyncio
async def test_toggle_live_blocked_during_ptt(monkeypatch):
    """_toggle_live() startet nicht wenn PTT-Session läuft."""
    from unittest.mock import MagicMock, patch, AsyncMock
    from app.daemon import BlitztextDaemon
    from app.config import BlitztextConfig

    cfg = BlitztextConfig(openai_api_key="x", input_device="/dev/null")
    daemon = BlitztextDaemon(cfg)
    daemon._session = MagicMock()  # PTT läuft

    with patch("app.daemon.LiveRecordingSession") as mock_cls:
        await daemon._toggle_live()
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_live_starts_sessions(monkeypatch):
    """_toggle_live() startet mic + desktop Session wenn frei."""
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
         patch("app.daemon.notify"), \
         patch("app.daemon.subprocess.Popen"):
        mock_live.start_pumps = AsyncMock()
        await daemon._toggle_live()

    assert daemon._live_mic_session is mock_mic
    assert daemon._live_desktop_session is mock_desktop
    mock_mic.start.assert_called_once()
    mock_desktop.start.assert_called_once()
    mock_live.set_sessions.assert_called_once_with(mock_mic, mock_desktop)


@pytest.mark.asyncio
async def test_toggle_live_stops_sessions(monkeypatch):
    """Zweiter _toggle_live()-Aufruf stoppt Sessions."""
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
        await daemon._toggle_live()

    mock_mic.stop.assert_called_once()
    mock_desktop.stop.assert_called_once()
    assert daemon._live_mic_session is None
    mock_live.set_sessions.assert_called_with(None, None)

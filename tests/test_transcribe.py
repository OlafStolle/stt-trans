# tests/test_transcribe.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.transcribe import transcribe_audio, local_available


# ---------------------------------------------------------------------------
# Existing online tests (unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# local_available()
# ---------------------------------------------------------------------------

def test_local_available_when_installed():
    """local_available() returns True when faster_whisper can be imported."""
    fake_module = MagicMock()
    with patch.dict("sys.modules", {"faster_whisper": fake_module}):
        assert local_available() is True


def test_local_available_when_not_installed():
    """local_available() returns False when faster_whisper is missing."""
    with patch.dict("sys.modules", {"faster_whisper": None}):
        # Setting the value to None makes the import raise ImportError
        assert local_available() is False


# ---------------------------------------------------------------------------
# transcribe_audio() dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transcribe_audio_uses_local_when_configured(tmp_path, monkeypatch):
    """When backend=local and faster-whisper available, local path is called."""
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))

    from app.config import load_config, save_config
    cfg = load_config()
    cfg = cfg.model_copy(update={"transcribe_backend": "local", "local_whisper_model": "small"})
    save_config(cfg)

    local_mock = AsyncMock(return_value="Lokales Ergebnis")

    with patch("app.transcribe.local_available", return_value=True), \
         patch("app.transcribe.transcribe_audio_local", local_mock):
        result = await transcribe_audio(b"wav", language="de")

    assert result == "Lokales Ergebnis"
    local_mock.assert_awaited_once_with(b"wav", "de", "small")


@pytest.mark.asyncio
async def test_transcribe_audio_falls_back_when_local_unavailable(tmp_path, monkeypatch):
    """When backend=local but faster-whisper missing, online path is used."""
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))

    from app.config import load_config, save_config
    cfg = load_config()
    cfg = cfg.model_copy(update={"transcribe_backend": "local"})
    save_config(cfg)

    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value="Online Fallback")

    with patch("app.transcribe.local_available", return_value=False), \
         patch("app.transcribe.get_client", return_value=mock_client):
        result = await transcribe_audio(b"wav", language="de")

    assert result == "Online Fallback"
    mock_client.audio.transcriptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_transcribe_audio_uses_online_by_default(tmp_path, monkeypatch):
    """Default backend=online always uses the online path."""
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))

    from app.config import load_config
    cfg = load_config()
    assert cfg.transcribe_backend == "online"

    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value="Online Text")

    with patch("app.transcribe.get_client", return_value=mock_client):
        result = await transcribe_audio(b"wav", language="de")

    assert result == "Online Text"


# ---------------------------------------------------------------------------
# FasterWhisperEngine
# ---------------------------------------------------------------------------

def test_faster_whisper_engine_joins_segments():
    """FasterWhisperEngine.transcribe() joins segment texts."""
    from app.transcribe import FasterWhisperEngine

    engine = FasterWhisperEngine()

    seg1 = MagicMock()
    seg1.text = " Hallo "
    seg2 = MagicMock()
    seg2.text = " Welt "

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())

    engine._model = mock_model
    engine._model_size = "small"

    result = engine.transcribe("/tmp/fake.wav", "de")
    assert result == "Hallo Welt"


def test_faster_whisper_engine_transcribe_raises_when_not_loaded():
    """transcribe() raises RuntimeError if model not loaded."""
    from app.transcribe import FasterWhisperEngine
    engine = FasterWhisperEngine()
    with pytest.raises(RuntimeError, match="not loaded"):
        engine.transcribe("/tmp/fake.wav", "de")

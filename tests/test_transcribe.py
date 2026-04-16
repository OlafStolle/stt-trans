# tests/test_transcribe.py
import pytest
from unittest.mock import AsyncMock, patch
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

# tests/test_process.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.process import process_text, ProcessMode

@pytest.mark.asyncio
async def test_process_plus():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Sehr geehrter Herr MP"
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.process.get_client", return_value=mock_client):
        result = await process_text("Ey MP was geht", ProcessMode.PLUS, prompt="Formuliere schriftlich:")
    assert len(result) > 0

@pytest.mark.asyncio
async def test_process_rage():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Guten Tag, ich wollte höflich nachfragen..."
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.process.get_client", return_value=mock_client):
        result = await process_text("DU IDIOT!", ProcessMode.RAGE, prompt="Mach nett:")
    assert "höflich" in result.lower() or len(result) > 5

@pytest.mark.asyncio
async def test_process_emoji_mittel():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Super Tag heute 🚀 alles läuft 😊"
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.process.get_client", return_value=mock_client):
        result = await process_text("Super Tag heute", ProcessMode.EMOJI, emoji_count="mittel")
    assert len(result) > 0

@pytest.mark.asyncio
async def test_process_normal_passthrough():
    """Normal mode: kein LLM, Text unveraendert zurueck."""
    result = await process_text("Hallo Welt", ProcessMode.NORMAL)
    assert result == "Hallo Welt"

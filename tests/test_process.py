# tests/test_process.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.process import process_text, ProcessMode, _strip_meta_lines, _call_claude_cli

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


def test_strip_meta_lines():
    """🔊-TTS-Zeilen werden entfernt, normaler Text bleibt erhalten."""
    text = "Sauberer Text\n🔊 Das hier ist eine Sprachausgabe-Zeile"
    assert _strip_meta_lines(text) == "Sauberer Text"
    # Reiner Text bleibt unveraendert (nur getrimmt)
    assert _strip_meta_lines("  Nur Text  ") == "Nur Text"


@pytest.mark.asyncio
async def test_call_claude_cli_uses_system_prompt_and_isolated_settings():
    """CLI-Aufruf nutzt --system-prompt + isolierte Settings, strippt Meta-Zeilen."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"sauberer text", b""))

    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=mock_proc),
    ) as mock_exec:
        result = await _call_claude_cli("SYSPROMPT", "roher text", "sonnet")

    assert result == "sauberer text"
    args = mock_exec.call_args.args
    assert "--system-prompt" in args
    assert "SYSPROMPT" in args
    assert "--setting-sources" in args
    assert "" in args
    assert "roher text" in args
    assert "-p" in args

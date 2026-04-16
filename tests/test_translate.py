import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_translate_calls_gpt(monkeypatch):
    """translate() ruft GPT-4o-mini auf und gibt Übersetzung zurück."""
    mock_response = AsyncMock()
    mock_response.choices[0].message.content = "Hallo Welt"

    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client_fn.return_value = mock_client

        from app.translate import translate
        result = await translate("Hello world", "de")

    assert result == "Hallo Welt"


@pytest.mark.asyncio
async def test_translate_same_lang_skipped():
    """Wenn Zielsprache == Quellsprache, wird Originaltext zurückgegeben ohne API-Aufruf."""
    from app.translate import translate
    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client_fn.side_effect = AssertionError("Should not be called")
        result = await translate("Hallo", "de", source_lang="de")
    assert result == "Hallo"


@pytest.mark.asyncio
async def test_translate_timeout_returns_original(monkeypatch):
    """Bei Timeout gibt translate() den Originaltext zurück."""
    import asyncio

    async def slow_create(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = slow_create
        mock_client_fn.return_value = mock_client

        from app import translate as translate_module
        original_timeout = translate_module.TRANSLATE_TIMEOUT
        translate_module.TRANSLATE_TIMEOUT = 0.01
        try:
            from app.translate import translate
            result = await translate("Hello", "de")
        finally:
            translate_module.TRANSLATE_TIMEOUT = original_timeout

    assert result == "Hello"


@pytest.mark.asyncio
async def test_translate_cebuano():
    """Cebuano-Übersetzung funktioniert (prompt enthält 'Cebuano/Bisaya')."""
    mock_response = AsyncMock()
    mock_response.choices[0].message.content = "Kumusta kalibutan"

    with patch("app.translate.get_openai_client") as mock_client_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client_fn.return_value = mock_client

        from app.translate import translate
        result = await translate("Hello world", "ceb")

        call_kwargs = mock_client.chat.completions.create.call_args
        prompt = call_kwargs.kwargs["messages"][0]["content"]
        assert "Cebuano" in prompt or "Bisaya" in prompt

    assert result == "Kumusta kalibutan"

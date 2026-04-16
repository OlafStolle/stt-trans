# app/translate.py
"""GPT-4o-mini Übersetzungs-Wrapper für Live-Transkription."""
import asyncio
import logging

from openai import AsyncOpenAI
from app.config import load_config

logger = logging.getLogger("stt-trans.translate")

TRANSLATE_TIMEOUT = 5.0  # Sekunden

_LANG_NAMES = {
    "de": "German",
    "en": "English",
    "ceb": "Cebuano/Bisaya",
}

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client


async def translate(
    text: str,
    target_lang: str,
    source_lang: str | None = None,
) -> str:
    """Übersetzt text in target_lang (de/en/ceb) via GPT-4o-mini.

    Gibt Originaltext zurück bei:
    - source_lang == target_lang
    - Timeout (>5s)
    - API-Fehler

    Args:
        text: Zu übersetzender Text.
        target_lang: Zielsprache: "de", "en" oder "ceb".
        source_lang: Quellsprache (optional, für Kurzschluss-Check).
    """
    if source_lang and source_lang == target_lang:
        return text
    if not text.strip():
        return text

    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    prompt = f"Translate to {lang_name}. Return only the translation, no explanation: {text}"

    try:
        client = get_openai_client()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0,
            ),
            timeout=TRANSLATE_TIMEOUT,
        )
        content = response.choices[0].message.content
        return content.strip() if content else text
    except asyncio.TimeoutError:
        logger.warning("translate: timeout nach %.1fs — Originaltext zurückgegeben", TRANSLATE_TIMEOUT)
        return text
    except Exception as e:
        logger.error("translate: Fehler: %s", e)
        return text

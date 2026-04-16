# app/process.py
"""LLM Post-Processing fuer Plus, Rage und Emoji-Modi."""
from enum import Enum
from openai import AsyncOpenAI
from app.config import load_config


class ProcessMode(str, Enum):
    NORMAL = "normal"
    PLUS   = "plus"
    RAGE   = "rage"
    EMOJI  = "emoji"


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """Return a singleton AsyncOpenAI client, initialised from config."""
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client


_EMOJI_COUNT_MAP = {
    "wenig":  "1 bis 2",
    "mittel": "3 bis 5",
    "viel":   "6 bis 10",
}


async def process_text(
    text: str,
    mode: ProcessMode,
    prompt: str | None = None,
    emoji_count: str = "mittel",
    model: str | None = None,
) -> str:
    """Post-process transcribed text via LLM according to the active mode.

    Args:
        text: Raw transcription text to process.
        mode: Processing mode (normal / plus / rage / emoji).
        prompt: Optional system prompt override for plus/rage modes.
        emoji_count: Emoji density for emoji mode ("wenig" | "mittel" | "viel").
        model: Override the LLM model from config.

    Returns:
        Processed text string. In NORMAL mode the input is returned unchanged.
    """
    if mode == ProcessMode.NORMAL:
        return text

    cfg = load_config()
    llm_model = model or cfg.llm_model
    client = get_client()

    if mode == ProcessMode.EMOJI:
        count_desc = _EMOJI_COUNT_MAP.get(emoji_count, "3 bis 5")
        system_prompt = (
            f"Fuege dem folgenden Text {count_desc} passende Emojis hinzu. "
            "Behalte den Text exakt bei, fuege nur Emojis an sinnvollen Stellen ein. "
            "Gib nur den fertigen Text zurueck, keine Erklaerungen."
        )
    else:
        system_prompt = (
            (prompt or "") +
            "\nGib nur den fertigen Text zurueck, keine Erklaerungen, kein Praefix."
        )

    resp = await client.chat.completions.create(
        model=llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": text},
        ],
        temperature=0.3,
        max_tokens=500,
    )
    return resp.choices[0].message.content.strip()

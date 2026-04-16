# app/transcribe.py
"""Whisper API Wrapper."""
import io
from openai import AsyncOpenAI
from app.config import load_config

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client


async def transcribe_audio(
    wav_bytes: bytes,
    language: str = "de",
    vocabulary: list[str] | None = None,
) -> str:
    """Sendet WAV-Bytes an Whisper, gibt transkribierten Text zurück.

    Args:
        wav_bytes: Raw WAV audio data to transcribe.
        language: BCP-47 language code, default "de" (German).
        vocabulary: Optional list of domain-specific words to bias recognition.

    Returns:
        Transcribed text as a plain string.
    """
    client = get_client()
    buf = io.BytesIO(wav_bytes)
    buf.name = "audio.wav"

    kwargs: dict = {
        "model": "whisper-1",
        "file": buf,
        "language": language,
        "response_format": "text",
    }
    if vocabulary:
        kwargs["prompt"] = ", ".join(vocabulary)

    result = await client.audio.transcriptions.create(**kwargs)
    # response_format="text" → SDK gibt direkt str zurück
    return result.strip() if isinstance(result, str) else str(result).strip()

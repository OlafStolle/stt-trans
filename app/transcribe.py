# app/transcribe.py
"""Whisper transcription — online (OpenAI API) and local (faster-whisper)."""
import io
import logging
import asyncio
import tempfile
import os
from typing import Literal

from openai import AsyncOpenAI
from app.config import load_config

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        _client = AsyncOpenAI(api_key=cfg.openai_api_key)
    return _client


# ---------------------------------------------------------------------------
# Local backend helpers
# ---------------------------------------------------------------------------

def local_available() -> bool:
    """Return True if faster-whisper is importable."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


class FasterWhisperEngine:
    """Singleton that holds a loaded WhisperModel in RAM."""

    def __init__(self) -> None:
        self._model = None
        self._model_size: str | None = None

    def ensure_loaded(self, model_size: str) -> None:
        """Load (or reload) the model if the requested size differs."""
        if self._model is not None and self._model_size == model_size:
            return
        if self._model is not None:
            logger.info("faster-whisper: unloading model '%s'", self._model_size)
            self._model = None

        from faster_whisper import WhisperModel
        logger.info("faster-whisper: loading model '%s' (may take a moment)", model_size)
        # device="auto" picks GPU when available, else CPU
        self._model = WhisperModel(model_size, device="auto", compute_type="int8")
        self._model_size = model_size
        logger.info("faster-whisper: model '%s' ready", model_size)

    def transcribe(self, wav_path: str, language: str) -> str:
        """Transcribe a WAV file; returns joined segment text."""
        if self._model is None:
            raise RuntimeError("Model not loaded — call ensure_loaded() first")
        segments, _ = self._model.transcribe(wav_path, language=language)
        return " ".join(seg.text.strip() for seg in segments).strip()

    def unload(self) -> None:
        self._model = None
        self._model_size = None


_fw_engine = FasterWhisperEngine()


async def transcribe_audio_local(
    wav_bytes: bytes,
    language: str,
    model_size: str,
) -> str:
    """Transcribe WAV bytes using faster-whisper (runs in executor)."""
    loop = asyncio.get_event_loop()

    def _run() -> str:
        _fw_engine.ensure_loaded(model_size)
        # Write to a temporary WAV file that faster-whisper can read
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        try:
            return _fw_engine.transcribe(tmp_path, language)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return await loop.run_in_executor(None, _run)


# ---------------------------------------------------------------------------
# Online backend (refactored — unchanged behavior)
# ---------------------------------------------------------------------------

async def _transcribe_online(
    wav_bytes: bytes,
    language: str = "de",
    vocabulary: list[str] | None = None,
) -> str:
    """Send WAV bytes to OpenAI Whisper API and return transcribed text."""
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
    # response_format="text" -> SDK returns str directly
    return result.strip() if isinstance(result, str) else str(result).strip()


# ---------------------------------------------------------------------------
# Public API — signature unchanged
# ---------------------------------------------------------------------------

async def transcribe_audio(
    wav_bytes: bytes,
    language: str = "de",
    vocabulary: list[str] | None = None,
) -> str:
    """Transcribe WAV bytes.

    Dispatches to the local faster-whisper backend or the OpenAI Whisper API
    depending on ``BlitztextConfig.transcribe_backend``.

    If the local backend is selected but faster-whisper is not installed,
    a WARNING is logged and the online path is used as fallback.

    Args:
        wav_bytes: Raw WAV audio data to transcribe.
        language: BCP-47 language code, default "de" (German).
        vocabulary: Optional list of domain-specific words to bias recognition
                    (online path only; ignored for local).

    Returns:
        Transcribed text as a plain string.
    """
    cfg = load_config()

    if cfg.transcribe_backend == "local":
        if not local_available():
            logger.warning(
                "faster-whisper is not installed — falling back to online transcription. "
                "Install with: pip install faster-whisper"
            )
        else:
            return await transcribe_audio_local(wav_bytes, language, cfg.local_whisper_model)

    return await _transcribe_online(wav_bytes, language, vocabulary)

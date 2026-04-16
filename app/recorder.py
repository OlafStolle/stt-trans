"""Audioaufnahme via sounddevice. Produziert WAV-Bytes fuer Whisper."""

import io
import threading

import numpy as np
import scipy.io.wavfile as wav
import sounddevice as sd

SAMPLERATE = 16000
CHANNELS = 1


def record_audio(
    duration_seconds: float = 3.0,
    samplerate: int = SAMPLERATE,
    device: str | None = None,
) -> bytes:
    """Nimmt duration_seconds Sekunden auf und gibt WAV-Bytes zurueck.

    Args:
        duration_seconds: Aufnahmedauer in Sekunden.
        samplerate: Abtastrate in Hz (Standard: 16000 fuer Whisper).
        device: PortAudio-Geraetename oder None fuer Systemstandard.

    Returns:
        WAV-kodierte Audiodaten als bytes.
    """
    frames = int(samplerate * duration_seconds)
    audio = sd.rec(
        frames,
        samplerate=samplerate,
        channels=CHANNELS,
        dtype="int16",
        device=device or None,
    )
    sd.wait()
    return _to_wav_bytes(audio, samplerate)


class RecordingSession:
    """Push-to-Talk Session: start() -> stop() -> WAV-Bytes.

    Attributes:
        samplerate: Abtastrate in Hz.
        device: PortAudio-Geraetename oder None.
    """

    def __init__(
        self,
        samplerate: int = SAMPLERATE,
        device: str | None = None,
    ) -> None:
        self.samplerate = samplerate
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Startet die Aufnahme. Kann mehrfach verwendet werden (reset)."""
        self._chunks.clear()
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=CHANNELS,
            dtype="int16",
            device=self.device or None,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        # Called from PortAudio thread — must not block.
        with self._lock:
            self._chunks.append(indata.copy())

    def stop(self) -> bytes:
        """Stoppt die Aufnahme und gibt WAV-Bytes zurueck.

        Returns:
            WAV-kodierte Audiodaten als bytes, oder b"" wenn keine Daten.
        """
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return b""
            audio = np.concatenate(self._chunks, axis=0)
        return _to_wav_bytes(audio, self.samplerate)


def _to_wav_bytes(audio: np.ndarray, samplerate: int) -> bytes:
    """Konvertiert ein NumPy-Array in WAV-Bytes.

    Args:
        audio: int16 Audio-Array, Shape (frames, channels).
        samplerate: Abtastrate in Hz.

    Returns:
        WAV-Datei als bytes.
    """
    buf = io.BytesIO()
    wav.write(buf, samplerate, audio)
    return buf.getvalue()

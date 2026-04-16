"""Audioaufnahme via sounddevice. Produziert WAV-Bytes fuer Whisper."""

import asyncio
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

    def stop(self, trailing_ms: int = 300) -> bytes:
        """Stoppt die Aufnahme und gibt WAV-Bytes zurueck.

        Args:
            trailing_ms: Stille-Padding am Ende in Millisekunden (verhindert Abschneiden).

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
        padding = np.zeros((int(self.samplerate * trailing_ms / 1000), CHANNELS), dtype=np.int16)
        audio = np.concatenate([audio, padding], axis=0)
        return _to_wav_bytes(audio, self.samplerate)


def find_monitor_device() -> str | None:
    """Gibt den Namen des PipeWire Monitor-Source-Geräts zurück, oder None.

    Versucht zuerst ALSA-Gerätenamen, dann pactl (PipeWire/PulseAudio).
    Rückgabe mit Präfix 'pulse:' → LiveRecordingSession muss PULSE_SOURCE setzen.
    """
    for dev in sd.query_devices():
        if "monitor" in dev["name"].lower() and dev["max_input_channels"] > 0:
            return dev["name"]
    # Fallback: PipeWire-Monitor via pactl suchen
    try:
        import subprocess as _sp
        result = _sp.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 5 and ".monitor" in parts[1] and parts[4] == "RUNNING":
                return f"pulse:{parts[1]}"
    except Exception:
        pass
    return None


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


class LiveRecordingSession:
    """Streamt Audio kontinuierlich in 4s-WAV-Chunks via asyncio.Queue.

    Verwendung:
        session = LiveRecordingSession(device="default")
        session.start(asyncio.get_running_loop())
        # WS-Handler liest:
        while True:
            chunk = await session.queue.get()
            if chunk is None:
                break  # Session beendet
            # chunk ist WAV-Bytes
        session.stop()
    """

    CHUNK_SECONDS = 4

    def __init__(
        self,
        samplerate: int = SAMPLERATE,
        device: str | None = None,
    ) -> None:
        self.samplerate = samplerate
        self._device = device
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._muted = False
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer: list[np.ndarray] = []
        self._chunk_frames = samplerate * self.CHUNK_SECONDS

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Startet den sounddevice-InputStream. loop nötig für thread-sichere Queue."""
        import os
        self._loop = loop
        self._buffer.clear()

        device = self._device
        _restore_pulse: str | None = os.environ.get("PULSE_SOURCE")
        if device and device.startswith("pulse:"):
            # PipeWire/PulseAudio Monitor-Source — PULSE_SOURCE setzen
            os.environ["PULSE_SOURCE"] = device[6:]
            device = "pulse"

        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=CHANNELS,
                dtype="int16",
                device=device or None,
                callback=self._callback,
            )
            self._stream.start()
        finally:
            # PULSE_SOURCE zurücksetzen
            if self._device and self._device.startswith("pulse:"):
                if _restore_pulse is None:
                    os.environ.pop("PULSE_SOURCE", None)
                else:
                    os.environ["PULSE_SOURCE"] = _restore_pulse

    def _callback(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        """PortAudio-Thread: Samples akkumulieren, bei vollem Chunk in Queue."""
        if self._muted:
            self._buffer.append(np.zeros_like(indata))
        else:
            self._buffer.append(indata.copy())

        total = sum(a.shape[0] for a in self._buffer)
        if total >= self._chunk_frames:
            full = np.concatenate(self._buffer, axis=0)
            audio = full[: self._chunk_frames]
            tail = full[self._chunk_frames :]
            self._buffer = [tail] if tail.shape[0] > 0 else []
            wav_bytes = _to_wav_bytes(audio, self.samplerate)
            if self._loop:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, wav_bytes)

    def stop(self) -> None:
        """Stoppt den Stream und legt None-Sentinel in die Queue."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._loop:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def set_muted(self, muted: bool) -> None:
        """Schaltet Mikrofon stumm (schreibt Stille statt echtem Audio)."""
        self._muted = muted

    @property
    def queue(self) -> asyncio.Queue[bytes | None]:
        """WS-Handler liest hieraus. None = Session beendet."""
        return self._queue

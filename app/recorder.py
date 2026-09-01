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
        # Detect device's native sample rate (may differ from 16000 Hz target).
        try:
            if device is not None:
                native_sr = sd.query_devices(device)["default_samplerate"]
            else:
                native_sr = float(SAMPLERATE)
        except Exception:
            native_sr = float(SAMPLERATE)
        self._native_sr: int = int(native_sr)
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Startet die Aufnahme. Kann mehrfach verwendet werden (reset)."""
        self._chunks.clear()
        self._stream = sd.InputStream(
            samplerate=self._native_sr,  # use device's native rate
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
            WAV-kodierte Audiodaten als bytes (16000 Hz), oder b"" wenn keine Daten.
        """
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return b""
            audio = np.concatenate(self._chunks, axis=0)
        # Padding at native rate, then resample everything to 16000 Hz for Whisper.
        padding = np.zeros((int(self._native_sr * trailing_ms / 1000), CHANNELS), dtype=np.int16)
        audio = np.concatenate([audio, padding], axis=0)
        if self._native_sr != SAMPLERATE:
            audio = _resample(audio, self._native_sr, SAMPLERATE)
        return _to_wav_bytes(audio, SAMPLERATE)


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
            if len(parts) >= 2 and ".monitor" in parts[1]:
                return f"pulse:{parts[1]}"
    except Exception:
        pass
    return None


def _resample(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Resamples int16 audio from from_sr to to_sr using linear interpolation.

    Args:
        audio: int16 array, shape (frames, channels).
        from_sr: Source sample rate in Hz.
        to_sr: Target sample rate in Hz.

    Returns:
        Resampled int16 array, shape (new_frames, channels).
    """
    n_in = audio.shape[0]
    n_out = int(n_in * to_sr / from_sr)
    x_in = np.arange(n_in)
    x_out = np.linspace(0, n_in - 1, n_out)
    if audio.ndim == 1:
        return np.interp(x_out, x_in, audio).astype(np.int16)
    # Multi-channel: resample each channel independently.
    channels = audio.shape[1]
    out = np.empty((n_out, channels), dtype=np.int16)
    for c in range(channels):
        out[:, c] = np.interp(x_out, x_in, audio[:, c]).astype(np.int16)
    return out


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
    """Streamt Audio als WAV-Chunks via asyncio.Queue — geschnitten an Sprechpausen.

    Ein starres Zeitraster (frueher: alle 4 Sekunden) trennt mitten im Wort.
    Whisper bekommt dann Bruchstuecke und raet: aus "Kundenprojekt" wurde "Kuhn".
    Deshalb wird hier an der naechsten Sprechpause geschnitten:

      * kuerzer als MIN_SECONDS      -> weiterlaufen lassen (keine Schnipsel)
      * Pause laenger als SILENCE_MS -> schneiden, das Wort ist zu Ende
      * laenger als MAX_SECONDS      -> Notschnitt mit kleiner Ueberlappung,
                                        damit auch bei Dauerreden nichts verloren geht

    Verwendung:
        session = LiveRecordingSession(device="default")
        session.start(asyncio.get_running_loop())
        while True:
            chunk = await session.queue.get()
            if chunk is None:
                break  # Session beendet
        session.stop()
    """

    CHUNK_SECONDS = 4          # beibehalten: Bezugsgroesse fuer bestehende Aufrufer
    MIN_SECONDS = 2.0          # darunter wird nicht geschnitten
    MAX_SECONDS = 12.0         # Notbremse bei durchgehender Rede
    SILENCE_MS = 400           # Pausenlaenge, die einen Schnitt ausloest
    OVERLAP_SECONDS = 0.5      # nur beim Notschnitt: Sicherheitsueberlappung
    #: Alles darunter gilt als Stille (int16-Skala, ~0.5 % Vollausschlag).
    SILENCE_LEVEL = 180

    def __init__(
        self,
        samplerate: int = SAMPLERATE,
        device: str | None = None,
    ) -> None:
        self.samplerate = samplerate
        self._device = device
        # Detect device's native sample rate (may differ from 16000 Hz target).
        # For pulse: prefixed devices the real name isn't queryable here; fall back to SAMPLERATE.
        try:
            if device is not None and not device.startswith("pulse:"):
                native_sr = sd.query_devices(device)["default_samplerate"]
            else:
                native_sr = float(SAMPLERATE)
        except Exception:
            native_sr = float(SAMPLERATE)
        self._native_sr: int = int(native_sr)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._muted = False
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer: list[np.ndarray] = []
        # Chunk length in native-rate frames.
        self._chunk_frames = self._native_sr * self.CHUNK_SECONDS
        self._min_frames = int(self._native_sr * self.MIN_SECONDS)
        self._max_frames = int(self._native_sr * self.MAX_SECONDS)
        self._silence_frames = int(self._native_sr * self.SILENCE_MS / 1000)
        self._overlap_frames = int(self._native_sr * self.OVERLAP_SECONDS)
        self._quiet_run = 0        # zusammenhaengende stille Frames am Pufferende
        self._buffered = 0         # Frames im Puffer (spart staendiges Summieren)

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
                samplerate=self._native_sr,  # use device's native rate
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
        """PortAudio-Thread: Samples sammeln und an der naechsten Pause schneiden.

        Muss schnell sein — laeuft im Audio-Thread. Deshalb nur ein Mittelwert
        ueber den Block statt echter Signalanalyse.
        """
        block = np.zeros_like(indata) if self._muted else indata.copy()
        self._buffer.append(block)
        self._buffered += block.shape[0]

        # Stille am Pufferende mitzaehlen; jeder laute Block setzt zurueck.
        if self._muted or np.abs(block).mean() < self.SILENCE_LEVEL:
            self._quiet_run += block.shape[0]
        else:
            self._quiet_run = 0

        pause = (self._buffered >= self._min_frames
                 and self._quiet_run >= self._silence_frames)
        notschnitt = self._buffered >= self._max_frames
        if not (pause or notschnitt):
            return

        full = np.concatenate(self._buffer, axis=0)
        if notschnitt and not pause:
            # Mitten im Wort: ein Stueck doppelt senden, damit das Wort in einem
            # der beiden Chunks vollstaendig vorkommt.
            cut = full.shape[0] - self._overlap_frames
            audio, rest = full[:], full[max(0, cut):]
        else:
            audio, rest = full, np.empty((0, CHANNELS), dtype=full.dtype)

        self._buffer = [rest] if rest.shape[0] else []
        self._buffered = int(rest.shape[0])
        self._quiet_run = 0

        self._emit(audio)

    def _emit(self, audio: np.ndarray) -> None:
        """Legt einen fertigen Abschnitt als WAV in die Queue."""
        if audio.shape[0] == 0:
            return
        if self._native_sr != SAMPLERATE:
            audio = _resample(audio, self._native_sr, SAMPLERATE)
        wav_bytes = _to_wav_bytes(audio, SAMPLERATE)
        if self._loop:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, wav_bytes)

    def stop(self) -> None:
        """Stoppt den Stream und legt None-Sentinel in die Queue.

        Der angefangene Abschnitt wird vorher noch abgeschickt — sonst fehlt der
        letzte Satz, der beim Beenden noch im Puffer stand.
        """
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._buffer:
            rest = np.concatenate(self._buffer, axis=0)
            self._buffer, self._buffered = [], 0
            # Zu kurze Reste sind reine Atemgeraeusche.
            if rest.shape[0] >= int(self._native_sr * 0.4):
                self._emit(rest)
        if self._loop:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def set_muted(self, muted: bool) -> None:
        """Schaltet Mikrofon stumm (schreibt Stille statt echtem Audio)."""
        self._muted = muted

    @property
    def queue(self) -> asyncio.Queue[bytes | None]:
        """WS-Handler liest hieraus. None = Session beendet."""
        return self._queue

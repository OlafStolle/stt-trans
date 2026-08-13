"""Der Live-Schnitt trennt an Sprechpausen statt im starren Zeitraster."""
import asyncio

import numpy as np
import pytest

from app.recorder import CHANNELS, LiveRecordingSession


class _Loop:
    """Nimmt die Queue-Aufrufe entgegen, ohne echten Event-Loop."""

    def call_soon_threadsafe(self, fn, *a):
        fn(*a)


def _session(rate=16000):
    s = LiveRecordingSession(samplerate=rate)
    s._native_sr = rate
    s._min_frames = int(rate * s.MIN_SECONDS)
    s._max_frames = int(rate * s.MAX_SECONDS)
    s._silence_frames = int(rate * s.SILENCE_MS / 1000)
    s._overlap_frames = int(rate * s.OVERLAP_SECONDS)
    s._loop = _Loop()
    return s


def _block(rate, seconds, level):
    n = int(rate * seconds)
    return np.full((n, CHANNELS), level, dtype=np.int16)


def _feed(s, rate, seconds, level):
    """Speist Audio in 20-ms-Bloecken ein, wie es der Audiotreiber tut."""
    step = 0.02
    for _ in range(int(seconds / step)):
        s._callback(_block(rate, step, level), 0, None, None)


def _drain(s):
    out = []
    while not s.queue.empty():
        out.append(s.queue.get_nowait())
    return out


def _dauer(wav: bytes, rate=16000) -> float:
    import io
    import scipy.io.wavfile as wavfile
    sr, d = wavfile.read(io.BytesIO(wav))
    return len(d) / sr


RATE = 16000
LAUT = 6000
STILL = 20


def test_no_cut_inside_speech():
    """Durchgehende Rede von 4 s darf NICHT geschnitten werden (früher: harter Schnitt)."""
    s = _session()
    _feed(s, RATE, 4.0, LAUT)
    assert _drain(s) == [], "mitten in der Rede geschnitten"


def test_cut_at_pause():
    s = _session()
    _feed(s, RATE, 3.0, LAUT)     # Rede
    _feed(s, RATE, 0.6, STILL)    # Pause > SILENCE_MS
    chunks = _drain(s)
    assert len(chunks) == 1
    assert 3.2 < _dauer(chunks[0]) < 4.0, f"unerwartete Länge {_dauer(chunks[0]):.2f}s"


def test_short_pause_does_not_cut():
    """Eine Atempause von 200 ms ist kein Satzende."""
    s = _session()
    _feed(s, RATE, 3.0, LAUT)
    _feed(s, RATE, 0.2, STILL)
    _feed(s, RATE, 1.0, LAUT)
    assert _drain(s) == []


def test_pause_before_minimum_does_not_cut():
    """Unter MIN_SECONDS entstehen keine Schnipsel."""
    s = _session()
    _feed(s, RATE, 0.8, LAUT)
    _feed(s, RATE, 1.0, STILL)
    assert _drain(s) == []


def test_emergency_cut_on_long_speech():
    """Wer durchredet, blockiert die Ausgabe nicht."""
    s = _session()
    _feed(s, RATE, 13.0, LAUT)
    chunks = _drain(s)
    assert len(chunks) >= 1
    assert _dauer(chunks[0]) >= s.MAX_SECONDS - 0.2


def test_emergency_cut_overlaps():
    """Beim Notschnitt überlappt es, damit kein Wort zwischen die Chunks fällt."""
    s = _session()
    _feed(s, RATE, s.MAX_SECONDS, LAUT)   # exakt bis zur Notbremse
    chunks = _drain(s)
    assert len(chunks) == 1
    # Der Schluss des gesendeten Abschnitts steht wieder am Pufferanfang.
    assert s._buffered == pytest.approx(s._overlap_frames, abs=RATE * 0.05), \
        f"{s._buffered} Frames im Puffer statt ~{s._overlap_frames}"


def test_emergency_cut_loses_no_audio():
    """Über einen Notschnitt hinweg darf kein Sample fehlen."""
    s = _session()
    gesendet = []
    s._emit = lambda a: gesendet.append(a.shape[0])   # type: ignore[assignment]
    _feed(s, RATE, s.MAX_SECONDS, LAUT)
    assert gesendet, "kein Notschnitt ausgelöst"
    # gesendete Frames + verbliebener Puffer >= eingespeiste Frames
    assert gesendet[0] + s._buffered >= int(RATE * s.MAX_SECONDS)


def test_stop_flushes_remaining_audio():
    """Der letzte Satz darf beim Beenden nicht im Puffer liegenbleiben."""
    s = _session()
    _feed(s, RATE, 2.5, LAUT)
    assert _drain(s) == []
    s.stop()
    chunks = [c for c in _drain(s) if c is not None]
    assert len(chunks) == 1
    assert _dauer(chunks[0]) > 2.0


def test_stop_drops_tiny_leftover():
    s = _session()
    _feed(s, RATE, 0.2, LAUT)
    s.stop()
    assert [c for c in _drain(s) if c is not None] == []


def test_stop_always_sends_sentinel():
    s = _session()
    _feed(s, RATE, 2.5, LAUT)
    s.stop()
    assert None in _drain(s), "Abschluss-Signal fehlt"


def test_muted_audio_counts_as_silence():
    """Stummgeschaltet entsteht kein Dauerpuffer."""
    s = _session()
    _feed(s, RATE, 3.0, LAUT)
    s.set_muted(True)
    _feed(s, RATE, 0.6, LAUT)   # Pegel egal — stumm zählt als still
    assert len(_drain(s)) == 1


def test_multiple_sentences_produce_multiple_chunks():
    s = _session()
    for _ in range(3):
        _feed(s, RATE, 2.5, LAUT)
        _feed(s, RATE, 0.5, STILL)
    assert len(_drain(s)) == 3

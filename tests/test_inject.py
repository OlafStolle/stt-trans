# tests/test_inject.py
import pytest
from app.inject import inject_text, notify


def _disable_clipboard(monkeypatch):
    """Helper: Clipboard-Helper findet keine Tools → bestehende Call-Anzahl bleibt stabil."""
    monkeypatch.setattr(
        "shutil.which",
        lambda name: None if name in ("wl-copy", "xclip", "xsel") else "/usr/bin/" + name,
    )
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def test_inject_xdotool(monkeypatch):
    _disable_clipboard(monkeypatch)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Hallo Welt", method="xdotool")
    assert any("xdotool" in str(c) for c in calls)
    assert any("Hallo Welt" in str(c) for c in calls)

def test_inject_xclip_fallback(monkeypatch):
    _disable_clipboard(monkeypatch)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Test", method="xclip+paste")
    assert len(calls) == 2

def test_notify(monkeypatch):
    _disable_clipboard(monkeypatch)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    notify("recording", "Aufnahme gestartet")
    assert any("notify-send" in str(c) for c in calls)

def test_inject_empty_string_is_noop(monkeypatch):
    _disable_clipboard(monkeypatch)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("", method="xdotool")
    assert len(calls) == 0

def test_inject_wtype_called_when_method_is_wtype(monkeypatch):
    _disable_clipboard(monkeypatch)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Hallo Welt", method="wtype")
    assert len(calls) == 1
    assert calls[0][0] == "wtype"
    assert "Hallo Welt" in calls[0]

def test_inject_xdotool_still_works(monkeypatch):
    _disable_clipboard(monkeypatch)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Test", method="xdotool")
    assert len(calls) >= 1
    assert calls[0][0] == "xdotool"


def test_inject_xdotool_copies_to_clipboard(monkeypatch):
    """xdotool-Inject muss trotzdem ins Clipboard kopieren."""
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    inject_text("Hallo", method="xdotool")
    # Erster Call = wl-copy (Clipboard), dann xdotool
    assert calls[0][0] == "wl-copy"
    assert calls[1][0] == "xdotool"


def test_inject_wtype_copies_to_clipboard(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    inject_text("Test", method="wtype")
    assert calls[0][0] == "wl-copy"
    assert calls[1][0] == "wtype"


def test_clipboard_missing_does_not_raise(monkeypatch):
    """Wenn kein Clipboard-Tool verfügbar ist, darf inject_text nicht crashen."""
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr("shutil.which", lambda name: None)  # nichts gefunden
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    inject_text("Test", method="xdotool")
    # Nur xdotool, kein Clipboard-Call
    assert len(calls) == 1
    assert calls[0][0] == "xdotool"


def test_empty_text_no_clipboard(monkeypatch):
    """Leerer Text → kein Clipboard-Write."""
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    inject_text("", method="xdotool")
    assert len(calls) == 0

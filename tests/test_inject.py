# tests/test_inject.py
import pytest
from unittest.mock import patch
from app.inject import inject_text, notify

def test_inject_xdotool(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Hallo Welt", method="xdotool")
    assert any("xdotool" in str(c) for c in calls)
    assert any("Hallo Welt" in str(c) for c in calls)

def test_inject_xclip_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("Test", method="xclip+paste")
    assert len(calls) == 2

def test_notify(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    notify("recording", "Aufnahme gestartet")
    assert any("notify-send" in str(c) for c in calls)

def test_inject_empty_string_is_noop(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    inject_text("", method="xdotool")
    assert len(calls) == 0

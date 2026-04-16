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

from unittest.mock import MagicMock

def test_inject_wtype_called_when_method_is_wtype():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        inject_text("Hallo Welt", method="wtype")
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "wtype"
    assert "Hallo Welt" in args

def test_inject_xdotool_still_works():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        inject_text("Test", method="xdotool")
    args = mock_run.call_args[0][0]
    assert args[0] == "xdotool"

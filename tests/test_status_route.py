# tests/test_status_route.py
import pytest
from fastapi.testclient import TestClient


def test_status_idle(monkeypatch):
    from app import main as app_main
    from app.daemon import BlitztextDaemon
    d = BlitztextDaemon()
    monkeypatch.setattr(app_main, "_daemon", d)
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recording"] is False
    assert data["mode"] is None


def test_status_recording(monkeypatch):
    from app import main as app_main
    from app.daemon import BlitztextDaemon
    d = BlitztextDaemon()
    d._active_mode = "normal"
    monkeypatch.setattr(app_main, "_daemon", d)
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recording"] is True
    assert data["mode"] == "normal"

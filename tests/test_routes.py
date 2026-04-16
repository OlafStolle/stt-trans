# tests/test_routes.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch("app.daemon.BlitztextDaemon.run", new_callable=AsyncMock):
        import importlib, app.main
        importlib.reload(app.main)
        return TestClient(app.main.create_app())


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "status" in r.json()


def test_get_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert "trigger_mode" in data
    assert "modes" in data


def test_patch_config(client):
    r = client.patch("/api/config", json={"whisper_language": "en"})
    assert r.status_code == 200
    r2 = client.get("/api/config")
    assert r2.json()["whisper_language"] == "en"


def test_reset_config(client):
    client.patch("/api/config", json={"whisper_language": "en"})
    r = client.post("/api/config/reset")
    assert r.status_code == 200
    assert r.json()["whisper_language"] == "de"

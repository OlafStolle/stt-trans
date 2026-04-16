# tests/test_main.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    with patch("app.daemon.BlitztextDaemon.run", new_callable=AsyncMock):
        from app.main import create_app
        import importlib
        import app.main
        importlib.reload(app.main)
        return TestClient(app.main.create_app())


def test_app_health_route_registered(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_app_config_route_registered(client):
    r = client.get("/api/config")
    assert r.status_code == 200


def test_app_process_route_registered(client):
    with patch("app.process.process_text", new_callable=AsyncMock, return_value="test"):
        r = client.post("/api/process/normal", json={"text": "Hallo"})
    assert r.status_code == 200

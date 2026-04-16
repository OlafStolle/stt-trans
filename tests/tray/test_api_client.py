import pytest
from unittest.mock import patch, MagicMock
from app.tray.api_client import BlitztextClient


def test_get_config_returns_dict():
    client = BlitztextClient("http://localhost:8765")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"trigger_mode": "hold", "openai_api_key": "sk-x"}
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=mock_resp):
        cfg = client.get_config()
    assert cfg["trigger_mode"] == "hold"


def test_patch_config_sends_data():
    client = BlitztextClient("http://localhost:8765")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"trigger_mode": "toggle"}
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.patch", return_value=mock_resp) as mock_patch:
        result = client.patch_config({"trigger_mode": "toggle"})
    mock_patch.assert_called_once()
    assert result["trigger_mode"] == "toggle"


def test_get_health_returns_dict():
    client = BlitztextClient("http://localhost:8765")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "healthy"}
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=mock_resp):
        h = client.get_health()
    assert h["status"] == "healthy"


def test_list_input_devices_returns_list():
    client = BlitztextClient("http://localhost:8765")
    with patch("glob.glob", return_value=["/dev/input/event0", "/dev/input/event3"]):
        with patch("evdev.InputDevice") as MockDev:
            MockDev.return_value.name = "Test Keyboard"
            devices = client.list_input_devices()
    assert isinstance(devices, list)


def test_get_status_returns_dict():
    client = BlitztextClient("http://localhost:8765")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"recording": False, "mode": None}
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=mock_resp):
        s = client.get_status()
    assert s["recording"] is False

# tests/test_config.py
import json, os, pytest
from pathlib import Path
from app.config import BlitztextConfig, load_config, save_config, reset_config

def test_load_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    cfg = load_config()
    assert cfg.trigger_mode == "hold"
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.whisper_language == "de"
    assert "normal" in cfg.modes
    config_file = tmp_path / "config.json"
    assert config_file.exists(), "load_config() should auto-save defaults"

def test_save_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg = cfg.model_copy(update={"whisper_language": "en"})
    save_config(cfg)
    cfg2 = load_config()
    assert cfg2.whisper_language == "en"

def test_reset(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg = cfg.model_copy(update={"whisper_language": "en"})
    save_config(cfg)
    reset_config()
    cfg2 = load_config()
    assert cfg2.whisper_language == "de"

def test_transcribe_backend_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    cfg = load_config()
    assert cfg.transcribe_backend == "online"

def test_local_whisper_model_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(tmp_path / "config.json"))
    cfg = load_config()
    assert cfg.local_whisper_model == "small"

def test_transcribe_backend_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg = cfg.model_copy(update={"transcribe_backend": "local", "local_whisper_model": "medium"})
    save_config(cfg)
    cfg2 = load_config()
    assert cfg2.transcribe_backend == "local"
    assert cfg2.local_whisper_model == "medium"

def test_old_config_without_backend_field_loads(tmp_path, monkeypatch):
    """Config-Dateien ohne transcribe_backend (Legacy) laden ohne Fehler."""
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    # Schreibe eine Config ohne das neue Feld
    legacy = {"openai_api_key": "sk-test", "whisper_language": "en", "modes": {}}
    path.write_text(json.dumps(legacy))
    cfg = load_config()
    assert cfg.transcribe_backend == "online"
    assert cfg.local_whisper_model == "small"

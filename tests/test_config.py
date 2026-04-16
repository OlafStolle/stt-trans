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

def test_save_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg.whisper_language = "en"
    save_config(cfg)
    cfg2 = load_config()
    assert cfg2.whisper_language == "en"

def test_reset(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("BLITZTEXT_CONFIG", str(path))
    cfg = load_config()
    cfg.whisper_language = "en"
    save_config(cfg)
    reset_config()
    cfg2 = load_config()
    assert cfg2.whisper_language == "de"

# app/config.py
import json, os
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional

CONFIG_DEFAULT_PATH = Path.home() / ".config" / "transcriptor" / "config.json"


class ModeConfig(BaseModel):
    key_code: int = 0
    key_name: str = ""
    prompt: Optional[str] = None
    emoji_count: str = "mittel"  # wenig | mittel | viel


class InjectConfig(BaseModel):
    method: str = "wtype"   # wtype | xdotool | xclip+paste
    delay_ms: int = 50


class BlitztextConfig(BaseModel):
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    whisper_language: str = "de"
    trigger_mode: str = "hold"       # hold | toggle
    input_device: str = ""
    audio_device: str = "default"
    modes: dict[str, ModeConfig] = Field(default_factory=lambda: {
        "normal": ModeConfig(key_code=183, key_name="KEY_F13"),
        "plus":   ModeConfig(key_code=184, key_name="KEY_F14",
                             prompt="Formuliere folgenden gesprochenen Text schriftlich um. "
                                    "Behalte den Sinn exakt bei, mache ihn nur schriftlicher:"),
        "rage":   ModeConfig(key_code=185, key_name="KEY_F15",
                             prompt="Wandle folgenden wütenden Text in eine freundliche, "
                                    "professionelle Formulierung um:"),
        "emoji":  ModeConfig(key_code=186, key_name="KEY_F16", emoji_count="mittel"),
    })
    vocabulary: list[str] = Field(default_factory=list)
    inject: InjectConfig = Field(default_factory=InjectConfig)


def _config_path() -> Path:
    return Path(os.getenv("BLITZTEXT_CONFIG", str(CONFIG_DEFAULT_PATH))).expanduser()


def save_config(cfg: BlitztextConfig) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.model_dump_json(indent=2))


def load_config() -> BlitztextConfig:
    p = _config_path()
    if p.exists():
        return BlitztextConfig.model_validate(json.loads(p.read_text()))
    cfg = BlitztextConfig(openai_api_key=os.getenv("OPENAI_API_KEY", ""))
    save_config(cfg)
    return cfg


def reset_config() -> BlitztextConfig:
    p = _config_path()
    if p.exists():
        p.unlink()
    return load_config()

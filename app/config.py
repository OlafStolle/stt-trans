# app/config.py
import json, os
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal, Optional

CONFIG_DEFAULT_PATH = Path.home() / ".config" / "transcriptor" / "config.json"


class ModeConfig(BaseModel):
    key_code: int = 0
    key_codes: list[int] = Field(default_factory=list)
    key_name: str = ""
    prompt: Optional[str] = None
    emoji_count: str = "mittel"  # wenig | mittel | viel

    @property
    def effective_key_codes(self) -> list[int]:
        return self.key_codes if self.key_codes else ([self.key_code] if self.key_code else [])


class InjectConfig(BaseModel):
    method: Literal["wtype", "xdotool", "xclip+paste", "ydotool", "wl-copy+paste"] = "wl-copy+paste"
    delay_ms: int = 50
    paste_shortcut: Literal["ctrl+v", "ctrl+shift+v"] = "ctrl+shift+v"


class BlitztextConfig(BaseModel):
    openai_api_key: str = ""
    llm_provider: Literal["openai", "ollama", "claude_cli"] = "openai"
    llm_base_url: str = ""  # nur bei provider=ollama relevant; leer = http://127.0.0.1:11434/v1
    llm_model: str = "gpt-4o-mini"
    whisper_language: str = "de"
    trigger_mode: str = "hold"       # hold | toggle
    input_device: str = ""
    audio_device: str = "default"
    transcribe_backend: Literal["online", "local"] = "online"
    local_whisper_model: Literal["tiny", "base", "small", "medium", "large-v3"] = "small"
    modes: dict[str, ModeConfig] = Field(default_factory=lambda: {
        "normal": ModeConfig(key_code=183, key_name="KEY_F13"),
        "plus":   ModeConfig(key_code=184, key_name="KEY_F14",
                             prompt="Formuliere folgenden gesprochenen Text nur geringfuegig schriftlich um. "
                                    "Behalte den Wortlaut weitgehend bei. Korrigiere nur offensichtliche "
                                    "Versprecher, Fuellwoerter und Grammatik. Keine inhaltlichen "
                                    "Ergaenzungen, keine Umformulierungen, die den Stil veraendern."),
        "rage":   ModeConfig(key_code=185, key_name="KEY_F15",
                             prompt="Wandle folgenden wütenden Text in eine freundliche, "
                                    "professionelle Formulierung um:"),
        "emoji":  ModeConfig(key_code=186, key_name="KEY_F16", emoji_count="mittel"),
        "translate_en": ModeConfig(
            key_code=0, key_name="",
            prompt=("Uebersetze den folgenden Text wortgetreu ins Englische. "
                    "Nur die Uebersetzung, keine Erklaerung, kein Praefix."),
        ),
        "translate_ceb": ModeConfig(
            key_code=0, key_name="",
            prompt=("Uebersetze den folgenden Text ins Cebuano (Bisaya, gesprochen "
                    "in Negros Occidental). Nur die Uebersetzung, keine Erklaerung, "
                    "kein Praefix."),
        ),
        "prompt": ModeConfig(
            key_code=0, key_name="",
            prompt=(
                "Wandle den folgenden gesprochenen Text in einen strukturierten, "
                "ausfuehrlichen User-Prompt fuer die Claude Code CLI um.\n\n"
                "Struktur:\n"
                "1. **Ziel**: Ein Satz, was erreicht werden soll.\n"
                "2. **Kontext**: Relevante Umgebung (Projekt, Technologie, Einschraenkungen) "
                "aus dem Originaltext. Keine Erfindungen — wenn Details fehlen, "
                "nicht spekulieren, sondern am Ende nachfragen.\n"
                "3. **Anforderungen**: Konkrete Must-haves als Bullet-Liste.\n"
                "4. **Akzeptanzkriterien**: Woran erkenne ich, dass es fertig ist "
                "(Tests, Verhalten, messbare Ergebnisse).\n\n"
                "Wenn im Originaltext wichtige Details fehlen, haenge diesen Block an "
                "(sonst weglassen):\n\n"
                "**Offene Fragen:**\n"
                "Frage 1: [konkrete Frage]\n"
                "  - A) [Antwort-Option]\n"
                "  - B) [Antwort-Option]\n"
                "  - C) [Antwort-Option]\n\n"
                "Maximal 3 Fragen. Antwort-Optionen sollen entweder Ja/Nein oder "
                "sachliche Auswahl sein, so dass ich mit 'A', 'B' oder 'C' "
                "schnell entscheiden kann.\n\n"
                "Gib NUR den fertigen Prompt zurueck, keine Einleitung, keine "
                "Meta-Kommentare, kein Markdown-Code-Fence."
            ),
        ),
    })
    vocabulary: list[str] = Field(default_factory=list)
    inject: InjectConfig = Field(default_factory=InjectConfig)
    live_key_codes: list[int] = Field(default_factory=list)
    live_key_name: str = ""


def _config_path() -> Path:
    return Path(os.getenv("BLITZTEXT_CONFIG", str(CONFIG_DEFAULT_PATH))).expanduser()


def save_config(cfg: BlitztextConfig) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.model_dump_json(indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


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


def migrate_key_codes(config_path: Path | None = None) -> None:
    """Migriert key_code -> key_codes für bestehende Config-Dateien (einmalig)."""
    p = config_path or _config_path()
    if not p.exists():
        return
    d = json.loads(p.read_text())
    changed = False
    for mode_val in d.get("modes", {}).values():
        if not mode_val.get("key_codes") and mode_val.get("key_code"):
            mode_val["key_codes"] = [mode_val["key_code"]]
            changed = True
    if changed:
        p.write_text(json.dumps(d, indent=2))

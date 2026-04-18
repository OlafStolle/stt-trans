# app/routes/config_routes.py
from fastapi import APIRouter, Body
from app.config import load_config, save_config, reset_config, BlitztextConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    cfg = load_config()
    data = cfg.model_dump()
    # Never expose the full API key over HTTP
    key = data.get("openai_api_key", "")
    data["openai_api_key"] = f"...{key[-4:]}" if len(key) > 4 else ("***" if key else "")
    return data


@router.patch("")
def patch_config(updates: dict = Body(...)):
    cfg = load_config()
    updated = cfg.model_dump()

    # Deep-merge for nested dicts so partial updates (z.B. nur ein Mode-Key)
    # nicht den ganzen Unter-Dict ersetzen und andere Modi ausloeschen.
    for key, value in updates.items():
        if key == "modes" and isinstance(value, dict) and isinstance(updated.get("modes"), dict):
            for mode_name, mode_update in value.items():
                if isinstance(mode_update, dict) and isinstance(updated["modes"].get(mode_name), dict):
                    updated["modes"][mode_name].update(mode_update)
                else:
                    updated["modes"][mode_name] = mode_update
        else:
            updated[key] = value

    new_cfg = BlitztextConfig.model_validate(updated)
    save_config(new_cfg)
    data = new_cfg.model_dump()
    key = data.get("openai_api_key", "")
    data["openai_api_key"] = f"...{key[-4:]}" if len(key) > 4 else ("***" if key else "")
    return data


@router.post("/reset")
def post_reset():
    return reset_config()

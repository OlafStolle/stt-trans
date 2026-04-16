# app/routes/config_routes.py
from fastapi import APIRouter, Body
from app.config import load_config, save_config, reset_config, BlitztextConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=BlitztextConfig)
def get_config():
    return load_config()


@router.patch("", response_model=BlitztextConfig)
def patch_config(updates: dict = Body(...)):
    cfg = load_config()
    updated = cfg.model_dump()
    updated.update(updates)
    new_cfg = BlitztextConfig.model_validate(updated)
    save_config(new_cfg)
    return new_cfg


@router.post("/reset", response_model=BlitztextConfig)
def post_reset():
    return reset_config()

# app/routes/health_routes.py
import shutil
from fastapi import APIRouter
from app.config import load_config

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    cfg = load_config()
    return {
        "status": "healthy",
        "version": "0.1.0",
        "api_key_set": bool(cfg.openai_api_key),
        "input_device": cfg.input_device or "not configured",
        "xdotool_available": shutil.which("xdotool") is not None,
        "trigger_mode": cfg.trigger_mode,
    }

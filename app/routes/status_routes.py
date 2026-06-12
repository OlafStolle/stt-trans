# app/routes/status_routes.py
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def get_status():
    from app.main import get_daemon
    daemon = get_daemon()
    if daemon is None:
        return {"recording": False, "mode": None}
    return {
        "recording": daemon._active_mode is not None,
        "mode": daemon._active_mode,
        "live": daemon._live_mic_session is not None,
    }

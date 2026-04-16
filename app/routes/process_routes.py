# app/routes/process_routes.py
from fastapi import APIRouter
from pydantic import BaseModel
from app.process import process_text, ProcessMode

router = APIRouter(prefix="/api/process", tags=["process"])


class ProcessRequest(BaseModel):
    text: str
    emoji_count: str = "mittel"


@router.post("/{mode}")
async def post_process(mode: str, req: ProcessRequest):
    from app.config import load_config
    cfg = load_config()
    mode_cfg = cfg.modes.get(mode)
    result = await process_text(
        req.text,
        ProcessMode(mode),
        prompt=mode_cfg.prompt if mode_cfg else None,
        emoji_count=req.emoji_count,
    )
    return {"text": result, "mode": mode}

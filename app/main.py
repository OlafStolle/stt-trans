# app/main.py
"""stt-trans Linux — FastAPI App mit Daemon-Lifespan."""
import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import load_config
from app.daemon import BlitztextDaemon
from app.routes.config_routes import router as config_router
from app.routes.health_routes import router as health_router
from app.routes.process_routes import router as process_router
from app.routes.status_routes import router as status_router
from app.routes.live import router as live_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("stt-trans.daemon").setLevel(logging.DEBUG)


class _StatusFilter(logging.Filter):
    """Unterdrückt /api/status Access-Log-Einträge (Tray-Polling-Spam)."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/status" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_StatusFilter())

_daemon: BlitztextDaemon | None = None


def get_daemon() -> "BlitztextDaemon | None":
    return _daemon


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    global _daemon
    cfg = load_config()
    _daemon = BlitztextDaemon(cfg)
    task = asyncio.create_task(_daemon.run())
    yield
    _daemon.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="stt-trans",
        version="0.1.0",
        description="Systemweiter Diktierdienst für Linux",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://localhost:8765", "http://127.0.0.1:8765"],
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(config_router)
    app.include_router(health_router)
    app.include_router(process_router)
    app.include_router(status_router)
    app.include_router(live_router)
    # Mermaid liegt lokal bei — die Workshop-Ansicht laedt nichts aus dem Netz.
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)

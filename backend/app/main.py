from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from .artnet import manager, rebuild_manager_sync
from .config import FRONTEND_DIST
from .db import init_db
from .engine import build_spec_from_effect, engine as effect_engine
from .routers import ai as ai_router
from .routers import auth as auth_router
from .routers import controllers as controllers_router
from .routers import designer as designer_router
from .routers import effects as effects_router
from .routers import lights as lights_router
from .routers import models as models_router
from .routers import palettes as palettes_router
from .routers import scenes as scenes_router
from .routers import state as state_router
from .routers import states as states_router
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dmx")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed()
    rebuild_manager_sync()
    await effect_engine.start()
    _resume_active_effects()
    log.info("dmx-control backend started")
    try:
        yield
    finally:
        await effect_engine.stop()
        manager.close()


def _resume_active_effects() -> None:
    """Re-play every effect marked ``is_active`` at process start."""
    from sqlmodel import Session, select

    from .db import engine as db_engine
    from .models import Effect, Palette

    with Session(db_engine) as sess:
        rows = sess.exec(select(Effect).where(Effect.is_active == True)).all()  # noqa: E712
        for row in rows:
            palette = None
            if row.palette_id is not None:
                palette = sess.get(Palette, row.palette_id)
            try:
                spec = build_spec_from_effect(row, palette)
                effect_engine.play(spec)
                log.info("resumed effect %s (%s)", row.id, row.name)
            except Exception:
                log.exception("failed to resume effect %s", row.id)


app = FastAPI(title="DMX Control", version="0.1.0", lifespan=lifespan)

app.include_router(auth_router.router)
app.include_router(controllers_router.router)
app.include_router(models_router.router)
app.include_router(lights_router.router)
app.include_router(palettes_router.router)
app.include_router(effects_router.router)
app.include_router(scenes_router.router)
app.include_router(states_router.router)
app.include_router(state_router.router)
app.include_router(ai_router.router)
app.include_router(designer_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Static SPA
# ---------------------------------------------------------------------------
INDEX_HTML = FRONTEND_DIST / "index.html"


def _mount_spa() -> None:
    if not FRONTEND_DIST.exists():
        log.warning("frontend/dist not found at %s; SPA will not be served", FRONTEND_DIST)
        return
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


_mount_spa()


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str, request: Request):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)

    # Serve any existing top-level static file (favicon, robots, etc.)
    # but never escape FRONTEND_DIST.
    if full_path:
        candidate = (FRONTEND_DIST / full_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            candidate = None  # type: ignore[assignment]
        if candidate and candidate.is_file():
            return FileResponse(candidate)

    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML, media_type="text/html")
    return JSONResponse(
        {
            "detail": "frontend not built",
            "hint": "Run `cd frontend && npm ci && npm run build`.",
        },
        status_code=503,
    )

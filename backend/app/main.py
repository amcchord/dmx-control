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
from .engine import engine as effect_engine
from .lua import ScriptError
from .routers import ai as ai_router
from .routers import auth as auth_router
from .routers import controllers as controllers_router
from .routers import designer as designer_router
from .routers import effect_chat as effect_chat_router
from .routers import effects as effects_router
from .routers import layers as layers_router
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
    """Re-play every persisted layer + every legacy ``is_active`` effect
    at process start.

    Two paths so a user upgrading from the pre-layer build keeps every
    saved show: any effect marked active that doesn't yet have an
    EffectLayer row is migrated into one ("base" layer with normal
    blend), then every layer row is compiled and pushed to the engine.
    Both legacy fields (``Effect.is_active``) and the new layer rows are
    kept in sync after the migration."""
    from sqlmodel import Session, select

    from .db import engine as db_engine
    from .engine import build_spec_from_layer
    from .models import Effect, EffectLayer, Palette

    with Session(db_engine) as sess:
        existing_layers_by_effect: dict[int, EffectLayer] = {}
        for layer in sess.exec(select(EffectLayer)).all():
            if layer.effect_id is not None:
                existing_layers_by_effect.setdefault(layer.effect_id, layer)

        # Migrate legacy is_active effects -> layer rows.
        active = sess.exec(
            select(Effect).where(Effect.is_active == True)  # noqa: E712
        ).all()
        z = 100
        for layer in existing_layers_by_effect.values():
            if layer.z_index >= z:
                z = layer.z_index + 100
        for row in active:
            if row.id in existing_layers_by_effect:
                continue
            new_layer = EffectLayer(
                effect_id=row.id,
                z_index=z,
                blend_mode="normal",
                opacity=1.0,
                target_channels=list(row.target_channels or ["rgb"]),
                spread=row.spread,
                light_ids=list(row.light_ids or []),
                targets=list(row.targets or []),
                palette_id=row.palette_id,
                is_active=True,
            )
            sess.add(new_layer)
            sess.flush()
            existing_layers_by_effect[row.id] = new_layer
            z += 100
            log.info(
                "migrated effect %s (%s) to layer %s",
                row.id, row.name, new_layer.id,
            )
        sess.commit()

        # Compile + start every persisted layer.
        for layer in sess.exec(
            select(EffectLayer).order_by(EffectLayer.z_index)
        ).all():
            if not layer.is_active:
                continue
            effect = (
                sess.get(Effect, layer.effect_id)
                if layer.effect_id is not None
                else None
            )
            if effect is None:
                log.warning(
                    "deleting orphan layer %s (no effect)", layer.id
                )
                sess.delete(layer)
                continue
            palette = None
            pid = layer.palette_id or effect.palette_id
            if pid is not None:
                palette = sess.get(Palette, pid)
            try:
                spec = build_spec_from_layer(layer, effect, palette)
                effect_engine.play(spec)
                log.info(
                    "resumed layer %s (%s @ z=%d)",
                    layer.id, spec.name, layer.z_index,
                )
            except ScriptError as exc:
                log.warning(
                    "skipping layer %s (%s) on resume: %s",
                    layer.id, effect.name, exc,
                )
            except Exception:
                log.exception("failed to resume layer %s", layer.id)
        sess.commit()


app = FastAPI(title="DMX Control", version="0.1.0", lifespan=lifespan)

app.include_router(auth_router.router)
app.include_router(controllers_router.router)
app.include_router(models_router.router)
app.include_router(lights_router.router)
app.include_router(palettes_router.router)
app.include_router(effects_router.router)
app.include_router(effects_router.ws_router)
app.include_router(layers_router.router)
app.include_router(layers_router.ws_router)
app.include_router(scenes_router.router)
app.include_router(states_router.router)
app.include_router(state_router.router)
app.include_router(ai_router.router)
app.include_router(designer_router.router)
app.include_router(effect_chat_router.router)


@app.get("/api/health")
def health() -> dict:
    """Live engine health: tick count, last tick time, dropped frames,
    active layers. Polled by the desktop Live rail and Me screen."""
    snap = effect_engine.health_snapshot()
    return {"ok": True, **snap}


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

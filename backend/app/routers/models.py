import io
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlmodel import Session, select

from ..artnet import rebuild_manager_sync
from ..auth import AuthDep
from ..config import MODEL_IMAGES_DIR
from ..db import get_session
from ..models import Light, LightModel, LightModelMode
from ..schemas import LightModelIn, LightModelModeIn, LightModelModeOut, LightModelOut

router = APIRouter(prefix="/api/models", tags=["models"], dependencies=[AuthDep])

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
IMAGE_MAX_DIM = 1024


def _image_path(mid: int) -> str:
    return str(MODEL_IMAGES_DIR / f"{mid}.webp")


def _image_url(m: LightModel):
    if not m.image_filename:
        return None
    # Cache-bust by mtime so replacing an image invalidates stale cached copies.
    path = MODEL_IMAGES_DIR / m.image_filename
    try:
        v = int(os.path.getmtime(path))
    except OSError:
        v = 0
    return f"/api/models/{m.id}/image?v={v}"


def _mode_out(row: LightModelMode) -> LightModelModeOut:
    return LightModelModeOut(
        id=row.id,
        name=row.name,
        channels=list(row.channels),
        channel_count=row.channel_count,
        is_default=row.is_default,
        layout=row.layout,
        color_policy=dict(row.color_policy or {}),
        color_table=row.color_table if isinstance(row.color_table, dict) else None,
    )


def _model_out(m: LightModel, modes: list[LightModelMode]) -> LightModelOut:
    return LightModelOut(
        id=m.id,
        name=m.name,
        channels=list(m.channels),
        channel_count=m.channel_count,
        builtin=m.builtin,
        image_url=_image_url(m),
        modes=[_mode_out(x) for x in modes],
    )


def _modes_for(sess: Session, mid: int) -> list[LightModelMode]:
    rows = sess.exec(
        select(LightModelMode).where(LightModelMode.model_id == mid).order_by(LightModelMode.id)
    ).all()
    # Default first, then insertion order.
    rows.sort(key=lambda r: (0 if r.is_default else 1, r.id or 0))
    return list(rows)


def _refresh_model_cache(m: LightModel, modes: list[LightModelMode]) -> None:
    """Copy the default mode's channels into the cached columns on LightModel."""
    default = next((x for x in modes if x.is_default), None)
    if default is None and modes:
        default = modes[0]
    if default is not None:
        m.channels = list(default.channels)
        m.channel_count = default.channel_count
    else:
        m.channels = []
        m.channel_count = 0


@router.get("")
def list_models(sess: Session = Depends(get_session)) -> list[LightModelOut]:
    rows = sess.exec(select(LightModel)).all()
    # Built-ins last; user models newest-first (higher id first).
    rows = sorted(rows, key=lambda m: (1 if m.builtin else 0, -(m.id or 0)))
    return [_model_out(m, _modes_for(sess, m.id)) for m in rows]


def _create_modes(
    sess: Session, model_id: int, modes: list[LightModelModeIn]
) -> list[LightModelMode]:
    created: list[LightModelMode] = []
    for mode in modes:
        row = LightModelMode(
            model_id=model_id,
            name=mode.name,
            channels=list(mode.channels),
            channel_count=len(mode.channels),
            is_default=mode.is_default,
            layout=mode.layout if isinstance(mode.layout, dict) else None,
            color_policy=dict(mode.color_policy or {}),
            color_table=(
                mode.color_table if isinstance(mode.color_table, dict) else None
            ),
        )
        sess.add(row)
        created.append(row)
    sess.flush()
    return created


@router.post("", status_code=201)
def create_model(payload: LightModelIn, sess: Session = Depends(get_session)) -> LightModelOut:
    modes_in = payload.modes or []
    if not modes_in:
        raise HTTPException(400, "at least one mode is required")
    m = LightModel(
        name=payload.name,
        channels=[],
        channel_count=0,
        builtin=False,
    )
    sess.add(m)
    sess.flush()
    created = _create_modes(sess, m.id, modes_in)
    _refresh_model_cache(m, created)
    sess.add(m)
    sess.commit()
    sess.refresh(m)
    modes = _modes_for(sess, m.id)
    return _model_out(m, modes)


@router.patch("/{mid}")
def update_model(
    mid: int, payload: LightModelIn, sess: Session = Depends(get_session)
) -> LightModelOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models are read-only; clone to edit")

    existing = {row.id: row for row in _modes_for(sess, mid)}
    incoming = payload.modes or []
    if not incoming:
        raise HTTPException(400, "at least one mode is required")

    seen_ids: set[int] = set()
    for mode_in in incoming:
        layout = mode_in.layout if isinstance(mode_in.layout, dict) else None
        color_table = (
            mode_in.color_table
            if isinstance(mode_in.color_table, dict)
            else None
        )
        if mode_in.id is not None and mode_in.id in existing:
            row = existing[mode_in.id]
            row.name = mode_in.name
            row.channels = list(mode_in.channels)
            row.channel_count = len(mode_in.channels)
            row.is_default = mode_in.is_default
            row.layout = layout
            row.color_policy = dict(mode_in.color_policy or {})
            row.color_table = color_table
            sess.add(row)
            seen_ids.add(mode_in.id)
        else:
            row = LightModelMode(
                model_id=mid,
                name=mode_in.name,
                channels=list(mode_in.channels),
                channel_count=len(mode_in.channels),
                is_default=mode_in.is_default,
                layout=layout,
                color_policy=dict(mode_in.color_policy or {}),
                color_table=color_table,
            )
            sess.add(row)

    removed_ids = set(existing.keys()) - seen_ids
    if removed_ids:
        in_use = sess.exec(
            select(Light).where(Light.mode_id.in_(removed_ids))
        ).first()
        if in_use is not None:
            blocker = existing[in_use.mode_id]
            raise HTTPException(
                400,
                f"mode '{blocker.name}' is in use by one or more lights",
            )
        for rid in removed_ids:
            sess.delete(existing[rid])

    sess.flush()
    m.name = payload.name
    modes = _modes_for(sess, mid)
    _refresh_model_cache(m, modes)
    sess.add(m)
    sess.commit()
    sess.refresh(m)
    rebuild_manager_sync()
    return _model_out(m, _modes_for(sess, mid))


@router.delete("/{mid}", status_code=204)
def delete_model(mid: int, sess: Session = Depends(get_session)) -> None:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models cannot be deleted")
    in_use = sess.exec(select(Light).where(Light.model_id == mid)).first()
    if in_use is not None:
        raise HTTPException(400, "model is in use by one or more lights")
    for row in _modes_for(sess, mid):
        sess.delete(row)
    if m.image_filename:
        try:
            os.unlink(MODEL_IMAGES_DIR / m.image_filename)
        except OSError:
            pass
    sess.delete(m)
    sess.commit()


@router.post("/{mid}/clone", status_code=201)
def clone_model(mid: int, sess: Session = Depends(get_session)) -> LightModelOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    clone = LightModel(
        name=f"{m.name} (copy)",
        channels=list(m.channels),
        channel_count=m.channel_count,
        builtin=False,
    )
    sess.add(clone)
    sess.flush()
    for mode in _modes_for(sess, mid):
        sess.add(
            LightModelMode(
                model_id=clone.id,
                name=mode.name,
                channels=list(mode.channels),
                channel_count=mode.channel_count,
                is_default=mode.is_default,
                layout=mode.layout if isinstance(mode.layout, dict) else None,
                color_policy=dict(mode.color_policy or {}),
                color_table=(
                    mode.color_table
                    if isinstance(mode.color_table, dict)
                    else None
                ),
            )
        )
    sess.commit()
    sess.refresh(clone)
    return _model_out(clone, _modes_for(sess, clone.id))


# ---------------------------------------------------------------------------
# Per-mode endpoints (fine-grained edits)
# ---------------------------------------------------------------------------


@router.post("/{mid}/modes", status_code=201)
def add_mode(
    mid: int, payload: LightModelModeIn, sess: Session = Depends(get_session)
) -> LightModelModeOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models are read-only; clone to edit")
    existing_names = {r.name.strip().lower() for r in _modes_for(sess, mid)}
    if payload.name.strip().lower() in existing_names:
        raise HTTPException(400, f"mode '{payload.name}' already exists")

    if payload.is_default:
        for r in _modes_for(sess, mid):
            if r.is_default:
                r.is_default = False
                sess.add(r)

    row = LightModelMode(
        model_id=mid,
        name=payload.name,
        channels=list(payload.channels),
        channel_count=len(payload.channels),
        is_default=payload.is_default,
        layout=payload.layout if isinstance(payload.layout, dict) else None,
        color_policy=dict(payload.color_policy or {}),
        color_table=(
            payload.color_table
            if isinstance(payload.color_table, dict)
            else None
        ),
    )
    sess.add(row)
    sess.flush()
    _refresh_model_cache(m, _modes_for(sess, mid))
    sess.add(m)
    sess.commit()
    sess.refresh(row)
    rebuild_manager_sync()
    return _mode_out(row)


@router.patch("/{mid}/modes/{mode_id}")
def update_mode(
    mid: int,
    mode_id: int,
    payload: LightModelModeIn,
    sess: Session = Depends(get_session),
) -> LightModelModeOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models are read-only; clone to edit")
    row = sess.get(LightModelMode, mode_id)
    if row is None or row.model_id != mid:
        raise HTTPException(404, "mode not found")

    other_names = {
        r.name.strip().lower()
        for r in _modes_for(sess, mid)
        if r.id != mode_id
    }
    if payload.name.strip().lower() in other_names:
        raise HTTPException(400, f"mode '{payload.name}' already exists")

    row.name = payload.name
    row.channels = list(payload.channels)
    row.channel_count = len(payload.channels)
    row.layout = payload.layout if isinstance(payload.layout, dict) else None
    row.color_policy = dict(payload.color_policy or {})
    row.color_table = (
        payload.color_table
        if isinstance(payload.color_table, dict)
        else None
    )
    if payload.is_default and not row.is_default:
        for r in _modes_for(sess, mid):
            if r.id != mode_id and r.is_default:
                r.is_default = False
                sess.add(r)
        row.is_default = True
    elif not payload.is_default and row.is_default:
        # Keep at least one default — ignore attempts to unset the sole default.
        others = [r for r in _modes_for(sess, mid) if r.id != mode_id]
        if others:
            row.is_default = False
            others[0].is_default = True
            sess.add(others[0])
    sess.add(row)
    sess.flush()
    _refresh_model_cache(m, _modes_for(sess, mid))
    sess.add(m)
    sess.commit()
    sess.refresh(row)
    rebuild_manager_sync()
    return _mode_out(row)


@router.delete("/{mid}/modes/{mode_id}", status_code=204)
def delete_mode(
    mid: int, mode_id: int, sess: Session = Depends(get_session)
) -> None:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models are read-only; clone to edit")
    row = sess.get(LightModelMode, mode_id)
    if row is None or row.model_id != mid:
        raise HTTPException(404, "mode not found")
    remaining = [r for r in _modes_for(sess, mid) if r.id != mode_id]
    if not remaining:
        raise HTTPException(400, "cannot delete the last mode on a model")
    in_use = sess.exec(select(Light).where(Light.mode_id == mode_id)).first()
    if in_use is not None:
        raise HTTPException(400, "mode is in use by one or more lights")
    was_default = row.is_default
    sess.delete(row)
    if was_default:
        remaining[0].is_default = True
        sess.add(remaining[0])
    sess.flush()
    _refresh_model_cache(m, _modes_for(sess, mid))
    sess.add(m)
    sess.commit()
    rebuild_manager_sync()


# ---------------------------------------------------------------------------
# Per-model image
# ---------------------------------------------------------------------------


def _process_image(raw: bytes) -> bytes:
    """Decode any supported image, fit to <=IMAGE_MAX_DIM on the long edge,
    and return WEBP bytes."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, f"invalid image: {exc}") from exc
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")
    long_edge = max(img.size)
    if long_edge > IMAGE_MAX_DIM:
        ratio = IMAGE_MAX_DIM / long_edge
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=85, method=4)
    return out.getvalue()


@router.post("/{mid}/image")
def upload_image(
    mid: int,
    file: UploadFile,
    sess: Session = Depends(get_session),
) -> LightModelOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    ctype = (file.content_type or "").lower()
    if ctype not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            400, f"unsupported image type '{file.content_type}'"
        )
    raw = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large (max 5 MB)")
    if not raw:
        raise HTTPException(400, "empty upload")
    encoded = _process_image(raw)

    filename = f"{mid}.webp"
    target = MODEL_IMAGES_DIR / filename
    tmp = target.with_suffix(".webp.tmp")
    tmp.write_bytes(encoded)
    os.replace(tmp, target)

    m.image_filename = filename
    sess.add(m)
    sess.commit()
    sess.refresh(m)
    return _model_out(m, _modes_for(sess, mid))


@router.delete("/{mid}/image")
def delete_image(mid: int, sess: Session = Depends(get_session)) -> LightModelOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.image_filename:
        try:
            os.unlink(MODEL_IMAGES_DIR / m.image_filename)
        except OSError:
            pass
    m.image_filename = None
    sess.add(m)
    sess.commit()
    sess.refresh(m)
    return _model_out(m, _modes_for(sess, mid))


@router.get("/{mid}/image", include_in_schema=False)
def get_image(mid: int, sess: Session = Depends(get_session)) -> FileResponse:
    m = sess.get(LightModel, mid)
    if m is None or not m.image_filename:
        raise HTTPException(404, "no image")
    path = MODEL_IMAGES_DIR / m.image_filename
    if not path.exists():
        raise HTTPException(404, "image file missing")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )

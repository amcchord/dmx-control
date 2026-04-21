import random

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager
from ..auth import AuthDep
from ..db import get_session
from ..models import Light, Palette
from ..schemas import ApplyPaletteRequest, PaletteIn, PaletteOut

router = APIRouter(prefix="/api/palettes", tags=["palettes"], dependencies=[AuthDep])


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return (r, g, b)


def _to_out(p: Palette) -> PaletteOut:
    return PaletteOut(id=p.id, name=p.name, colors=list(p.colors), builtin=p.builtin)


@router.get("")
def list_palettes(sess: Session = Depends(get_session)) -> list[PaletteOut]:
    rows = sess.exec(select(Palette).order_by(Palette.builtin.desc(), Palette.name)).all()
    return [_to_out(p) for p in rows]


@router.post("", status_code=201)
def create_palette(payload: PaletteIn, sess: Session = Depends(get_session)) -> PaletteOut:
    p = Palette(name=payload.name, colors=payload.colors, builtin=False)
    sess.add(p)
    sess.commit()
    sess.refresh(p)
    return _to_out(p)


@router.patch("/{pid}")
def update_palette(
    pid: int, payload: PaletteIn, sess: Session = Depends(get_session)
) -> PaletteOut:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    if p.builtin:
        raise HTTPException(400, "builtin palettes are read-only; clone to edit")
    p.name = payload.name
    p.colors = payload.colors
    sess.add(p)
    sess.commit()
    sess.refresh(p)
    return _to_out(p)


@router.delete("/{pid}", status_code=204)
def delete_palette(pid: int, sess: Session = Depends(get_session)) -> None:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    if p.builtin:
        raise HTTPException(400, "builtin palettes cannot be deleted")
    sess.delete(p)
    sess.commit()


@router.post("/{pid}/clone", status_code=201)
def clone_palette(pid: int, sess: Session = Depends(get_session)) -> PaletteOut:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    clone = Palette(name=f"{p.name} (copy)", colors=list(p.colors), builtin=False)
    sess.add(clone)
    sess.commit()
    sess.refresh(clone)
    return _to_out(clone)


@router.post("/{pid}/apply")
def apply_palette(
    pid: int, req: ApplyPaletteRequest, sess: Session = Depends(get_session)
) -> dict:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    if not req.light_ids:
        return {"updated": 0}
    if not p.colors:
        raise HTTPException(400, "palette has no colors")

    lights = list(sess.exec(select(Light).where(Light.id.in_(req.light_ids))).all())
    # Preserve order from request
    order = {lid: i for i, lid in enumerate(req.light_ids)}
    lights.sort(key=lambda l: order.get(l.id, 0))

    n = len(lights)
    colors = p.colors

    if req.mode == "random":
        picks = [random.choice(colors) for _ in range(n)]
    elif req.mode == "gradient":
        if len(colors) == 1 or n == 1:
            picks = [colors[0]] * n
        else:
            picks = []
            for i in range(n):
                t = i / (n - 1)
                # Linearly interpolate across palette stops
                pos = t * (len(colors) - 1)
                lo = int(pos)
                hi = min(lo + 1, len(colors) - 1)
                frac = pos - lo
                r1, g1, b1 = _hex_to_rgb(colors[lo])
                r2, g2, b2 = _hex_to_rgb(colors[hi])
                r = int(round(r1 + (r2 - r1) * frac))
                g = int(round(g1 + (g2 - g1) * frac))
                b = int(round(b1 + (b2 - b1) * frac))
                picks.append(f"#{r:02X}{g:02X}{b:02X}")
    else:  # cycle
        picks = [colors[i % len(colors)] for i in range(n)]

    for light, hex_color in zip(lights, picks):
        r, g, b = _hex_to_rgb(hex_color)
        light.r = r
        light.g = g
        light.b = b
        light.w = min(r, g, b)
        light.a = min(r, g) // 2
        light.on = True
        sess.add(light)
    sess.commit()
    for light in lights:
        sess.refresh(light)
        manager.set_light_state(
            light.id,
            {
                "r": light.r,
                "g": light.g,
                "b": light.b,
                "w": light.w,
                "a": light.a,
                "uv": light.uv,
                "dimmer": light.dimmer,
                "on": light.on,
            },
        )
    return {"updated": len(lights)}

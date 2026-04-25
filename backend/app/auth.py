from __future__ import annotations

import time

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import DMX_PASSWORD, SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, SESSION_SECRET

_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="dmx-session")


def _is_authenticated(request) -> bool:
    """Accepts a Starlette Request OR WebSocket; both expose ``.cookies``."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data and data.get("ok"))


def is_authenticated_request(request) -> bool:
    """Public helper used by the websocket preview to gate connections."""
    return _is_authenticated(request)


def require_auth(request: Request) -> None:
    if not _is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )


AuthDep = Depends(require_auth)


def login(response: Response, password: str) -> bool:
    if password != DMX_PASSWORD:
        return False
    token = _serializer.dumps({"ok": True, "t": int(time.time())})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # Caddy terminates TLS; cookie still works over the reverse proxy.
        path="/",
    )
    return True


def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def status_for(request: Request) -> bool:
    return _is_authenticated(request)

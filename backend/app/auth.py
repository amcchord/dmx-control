from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, select

from .config import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, SESSION_SECRET
from .db import engine
from .models import User

_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="dmx-session-v2")
_lock = threading.Lock()
_attempts: dict[str, deque] = defaultdict(deque)
COOKIE_SECURE = os.environ.get("DMX_COOKIE_SECURE", "true").lower() != "false"


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Use a password with 12 to 256 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def user_for(request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        with Session(engine) as sess:
            user = sess.get(User, int(data["uid"]))
            if user and user.enabled and user.session_version == data.get("v"):
                return user
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        pass
    return None


def is_authenticated_request(request) -> bool:
    # Also used by WebSocket preview and layer streams.
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        return False
    return user_for(request) is not None


def require_auth(request: Request) -> User:
    user = user_for(request)
    if not user:
        raise HTTPException(401, "Please sign in again")
    request.state.user = user
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Request origin does not match this console")
        path = request.url.path
        if user.role == "viewer":
            raise HTTPException(403, "This account can view the rig. Ask an administrator for operator access.")
        config_write = (
            path.startswith("/api/models") or path.startswith("/api/ai/models")
            or (path.startswith("/api/controllers") and not path.endswith("/blackout"))
            or (path.startswith("/api/lights") and request.method in {"PATCH", "DELETE"})
            or path in {"/api/lights", "/api/lights/reorder"}
            or path.startswith("/api/auth/users")
        )
        if config_write and user.role != "admin":
            raise HTTPException(403, "Only administrators can change rig configuration")
        from .operations import is_live_change, record_before
        if is_live_change(request.method, path):
            from .transitions import cancel
            cancel()
            record_before(f"{request.method} {path}")
    return user


AuthDep = Depends(require_auth)


def require_admin(request: Request) -> User:
    user = require_auth(request)
    if user.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def public_user(user: User) -> dict:
    return {"id": user.id, "username": user.username, "name": user.name, "role": user.role, "enabled": user.enabled}


def login(request: Request, response: Response, username: str, password: str) -> User | None:
    name = username.strip().lower()
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    keys = [f"ip:{ip}", f"name:{name}"]
    with _lock:
        for key in keys:
            q = _attempts[key]
            while q and q[0] < now - 300:
                q.popleft()
            if len(q) >= (20 if key.startswith("ip:") else 8):
                raise HTTPException(429, "Too many sign-in attempts. Try again in 5 minutes.", headers={"Retry-After": "300"})
        if len(_attempts) > 5000:
            for key in list(_attempts):
                if not _attempts[key] or _attempts[key][-1] < now - 300:
                    del _attempts[key]
        for key in keys:
            _attempts[key].append(now)
    with Session(engine) as sess:
        user = sess.exec(select(User).where(User.username == name)).first()
        # Equal work for an unknown username, avoiding a cheap account oracle.
        stored = user.password_hash if user else "scrypt$missing$" + "0" * 128
        valid = verify_password(password, stored)
        if not valid or not user or not user.enabled:
            return None
        with _lock:
            _attempts.pop(f"name:{name}", None)
            if _attempts.get(f"ip:{ip}"):
                _attempts[f"ip:{ip}"].pop()
        token = _serializer.dumps({"uid": user.id, "v": user.session_version})
        response.set_cookie(SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE_SECONDS,
                            httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/")
        return user


def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")


def status_for(request) -> bool:
    return user_for(request) is not None


def create_user(username: str, name: str, role: str, password: str) -> User:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,47}", username):
        raise ValueError("Username must be 2–48 letters, numbers, dots, dashes or underscores")
    if role not in {"admin", "operator", "viewer"}:
        raise ValueError("Choose admin, operator or viewer")
    with Session(engine) as sess:
        if sess.exec(select(User).where(User.username == username)).first():
            raise ValueError("That username is already in use")
        user = User(username=username, name=name.strip()[:80] or username, role=role, password_hash=hash_password(password))
        sess.add(user)
        sess.commit()
        sess.refresh(user)
        return user

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import auth
from ..db import get_session
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=48)
    password: str = Field(max_length=256)


class NewUser(BaseModel):
    username: str
    name: str = ""
    role: Literal["admin", "operator", "viewer"] = "operator"
    password: str


class UserUpdate(BaseModel):
    role: Literal["admin", "operator", "viewer"] | None = None
    enabled: bool | None = None
    password: str | None = None


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    from urllib.parse import urlparse
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Request origin does not match this console")
    user = auth.login(request, response, payload.username, payload.password)
    if not user:
        raise HTTPException(401, "Username or password is incorrect")
    return {"authenticated": True, "user": auth.public_user(user)}


@router.post("/logout")
def logout(response: Response):
    auth.logout(response)
    return {"authenticated": False, "user": None}


@router.get("/status")
def status(request: Request):
    user = auth.user_for(request)
    return {"authenticated": bool(user), "user": auth.public_user(user) if user else None}


@router.get("/users", dependencies=[Depends(auth.require_admin)])
def users(sess: Session = Depends(get_session)):
    return [auth.public_user(u) for u in sess.exec(select(User).order_by(User.username)).all()]


@router.post("/users", status_code=201, dependencies=[Depends(auth.require_admin)])
def add_user(payload: NewUser):
    try:
        return auth.public_user(auth.create_user(**payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/users/{uid}", dependencies=[Depends(auth.require_admin)])
def update_user(uid: int, payload: UserUpdate, request: Request, sess: Session = Depends(get_session)):
    user = sess.get(User, uid)
    if not user:
        raise HTTPException(404, "Account not found")
    # Never disable or demote the account being used for this request.
    if uid == request.state.user.id and (payload.enabled is False or payload.role not in {None, "admin"}):
        raise HTTPException(400, "Another administrator must change your access")
    if user.role == "admin" and (payload.enabled is False or payload.role not in {None, "admin"}):
        admins = sess.exec(select(User).where(User.role == "admin", User.enabled == True)).all()
        if len(admins) < 2:
            raise HTTPException(400, "Keep at least one enabled administrator")
    if payload.password is not None:
        try:
            user.password_hash = auth.hash_password(payload.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.role is not None:
        user.role = payload.role
    if payload.enabled is not None:
        user.enabled = payload.enabled
    user.session_version += 1
    sess.add(user)
    sess.commit()
    return auth.public_user(user)

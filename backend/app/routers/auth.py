from fastapi import APIRouter, Request, Response

from .. import auth
from ..schemas import AuthStatus, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> AuthStatus:
    ok = auth.login(response, payload.password)
    if not ok:
        response.status_code = 401
        return AuthStatus(authenticated=False)
    return AuthStatus(authenticated=True)


@router.post("/logout")
def logout(response: Response) -> AuthStatus:
    auth.logout(response)
    return AuthStatus(authenticated=False)


@router.get("/status")
def status(request: Request) -> AuthStatus:
    return AuthStatus(authenticated=auth.status_for(request))

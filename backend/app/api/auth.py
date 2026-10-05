import secrets

import pyotp
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.db import get_db
from app.core.deps import current_user
from app.core.errors import AppError
from app.core.security import (
    CSRF_COOKIE,
    ELEVATED_COOKIE,
    SESSION_COOKIE,
    check_csrf,
    passwords,
    set_cookie,
)
from app.models.entities import User
from app.schemas.auth import LoginInput, SetupInput, TotpInput, UserOut
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/csrf")
def csrf(response: Response):
    token = secrets.token_urlsafe(32)
    set_cookie(response, CSRF_COOKIE, token, 3600, httponly=False)
    return {"csrf_token": token}


@router.post("/login", dependencies=[Depends(check_csrf)])
def login(data: LoginInput, response: Response, db: Session = Depends(get_db)):
    user = AuthService.login(db, data.email, data.password)
    token, lifetime = AuthService.issue_token(user)
    set_cookie(response, SESSION_COOKIE, token, lifetime)
    response.delete_cookie(ELEVATED_COOKIE, path="/")
    return {"user": UserOut.of(user), "expires_in": lifetime}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    audit(user.id, "auth.me", {}, db=db)
    return UserOut.of(user)


@router.post("/logout", dependencies=[Depends(check_csrf)])
def logout(response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.session_version += 1
    db.commit()
    for name in (SESSION_COOKIE, ELEVATED_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")
    audit(user.id, "auth.logout", {}, db=db)
    return {"message": "All sessions for this account revoked"}


@router.post("/2fa/setup", dependencies=[Depends(check_csrf)])
def setup(
    data: SetupInput,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.totp_secret:
        raise AppError("TOTP_ALREADY_CONFIGURED", "Authenticator is already enrolled", 409)
    if not passwords.verify(data.password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Password is incorrect", 401)
    user.pending_totp_secret = pyotp.random_base32()
    db.commit()
    audit(user.id, "auth.totp_setup", {}, db=db)
    response.headers["Cache-Control"] = "no-store"
    return {
        "secret": user.pending_totp_secret,
        "provisioning_uri": pyotp.TOTP(user.pending_totp_secret).provisioning_uri(
            user.email, issuer_name="SAHYOG Intelligence"
        ),
    }


def elevate(data, request, response, user, db, enrollment=False):
    AuthService.verify_totp(db, user, data.code, enrollment)
    token, lifetime = AuthService.issue_token(user, "elevated", request.state.session_claims["jti"])
    set_cookie(response, ELEVATED_COOKIE, token, lifetime)
    return {"elevated": True, "expires_in": lifetime}


@router.post("/2fa/confirm", dependencies=[Depends(check_csrf)])
def confirm(
    data: TotpInput,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return elevate(data, request, response, user, db, enrollment=True)


@router.post("/2fa/verify", dependencies=[Depends(check_csrf)])
def verify(
    data: TotpInput,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return elevate(data, request, response, user, db)

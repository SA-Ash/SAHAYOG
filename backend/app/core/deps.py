import uuid

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import ELEVATED_COOKIE, SESSION_COOKIE, decode_token
from app.models.entities import User


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AppError("UNAUTHENTICATED", "Please sign in", 401)
    payload = decode_token(token)
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (ValueError, TypeError) as exc:
        raise AppError("UNAUTHENTICATED", "Invalid session", 401) from exc
    if not user or not user.is_active or user.session_version != payload["ver"]:
        raise AppError("UNAUTHENTICATED", "Session has been revoked", 401)
    if request.method == "GET":
        audit(user.id, "sensitive.read", {"path": request.url.path}, db=db)
    request.state.session_claims = payload
    request.state.audit_actor = user.id
    request.state.audit_db = db
    return user


def require_role(*roles):
    def guard(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role not in roles:
            audit(user.id, "access.denied", {"roles": list(roles)}, db=db)
            raise AppError("FORBIDDEN", "Your role does not permit this action", 403)
        return user

    return guard


def require_elevated(request: Request, user: User = Depends(current_user)):
    token = request.cookies.get(ELEVATED_COOKIE)
    if not token:
        raise AppError("TWO_FACTOR_REQUIRED", "Verify a current authenticator code", 403)
    payload = decode_token(token, "elevated")
    if (
        payload["sub"] != str(user.id)
        or payload["ver"] != user.session_version
        or payload.get("session_jti") != request.state.session_claims["jti"]
    ):
        raise AppError("TWO_FACTOR_REQUIRED", "Elevation does not match this session", 403)
    return user

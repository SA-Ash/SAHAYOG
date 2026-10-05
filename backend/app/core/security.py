import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request, Response
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.errors import AppError

passwords = CryptContext(schemes=["bcrypt"], deprecated="auto")
SESSION_COOKIE = "sahyog_session"
ELEVATED_COOKIE = "sahyog_elevated"
CSRF_COOKIE = "sahyog_csrf"


def issue_token(user, kind="session", session_jti=None):
    settings = get_settings()
    lifetime = settings.jwt_minutes if kind == "session" else settings.elevated_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "kind": kind,
        "ver": user.session_version,
        "jti": secrets.token_urlsafe(24),
        "iat": now,
        "exp": now + timedelta(minutes=lifetime),
        "iss": "sahyog-engine",
        "aud": "sahyog-ui",
    }
    if session_jti:
        payload["session_jti"] = session_jti
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), lifetime * 60


def decode_token(token, kind="session"):
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            issuer="sahyog-engine",
            audience="sahyog-ui",
            options={"require": ["sub", "kind", "ver", "jti", "iat", "exp"]},
        )
        if payload["kind"] != kind:
            raise jwt.InvalidTokenError()
        return payload
    except jwt.InvalidTokenError as exc:
        raise AppError(
            "UNAUTHENTICATED", "Session expired or invalid; please sign in", 401
        ) from exc


def set_cookie(response: Response, name: str, value: str, max_age: int, httponly=True):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=httponly,
        secure=get_settings().cookie_secure,
        samesite="strict",
        path="/",
    )


def check_csrf(request: Request):
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    origin = request.headers.get("origin")
    if origin and origin != get_settings().frontend_origin:
        raise AppError("CSRF_REJECTED", "Untrusted request origin", 403)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise AppError("CSRF_REJECTED", "Refresh the page before submitting", 403)

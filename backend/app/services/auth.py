import time

import pyotp
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import AppError
from app.core.security import issue_token, passwords
from app.models.entities import User

DUMMY_HASH = passwords.hash("not-an-account-password")


class AuthService:
    issue_token = staticmethod(issue_token)

    @staticmethod
    def login(db: Session, email: str, password: str):
        user = db.scalar(select(User).where(User.email == email.lower()))
        valid = passwords.verify(password, user.password_hash if user else DUMMY_HASH)
        if not user or not valid or not user.is_active:
            audit(None, "auth.login_failed", {}, db=db)
            raise AppError("INVALID_CREDENTIALS", "Email or password is incorrect", 401)
        audit(user.id, "auth.login", {}, db=db)
        return user

    @staticmethod
    def verify_totp(db: Session, user: User, code: str, enrollment=False):
        secret = user.pending_totp_secret if enrollment else user.totp_secret
        if not secret:
            raise AppError("TOTP_NOT_CONFIGURED", "Set up an authenticator first", 400)
        step = int(time.time()) // 30
        matched = next(
            (s for s in range(step - 1, step + 2) if pyotp.TOTP(secret).at(s * 30) == code), None
        )
        if matched is None:
            raise AppError("INVALID_TOTP", "Authenticator code is incorrect or expired", 401)
        # Conditional write makes replay protection atomic across workers.
        stmt = update(User).where(
            User.id == user.id, (User.last_totp_step.is_(None) | (User.last_totp_step < matched))
        )
        values = {"last_totp_step": matched}
        if enrollment:
            values.update(totp_secret=secret, pending_totp_secret=None)
        if db.execute(stmt.values(**values)).rowcount != 1:
            db.rollback()
            raise AppError("TOTP_REPLAY", "This code was already used; wait for the next code", 409)
        db.commit()
        db.refresh(user)
        audit(user.id, "auth.totp_enrolled" if enrollment else "auth.totp_verified", {}, db=db)

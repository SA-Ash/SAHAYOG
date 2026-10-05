import argparse

import pyotp
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.entities import User

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Local demo-only authenticator helper; never expose via HTTP"
    )
    parser.add_argument("--email", default="supervisor@sahyog.demo")
    parser.add_argument("--provision", action="store_true")
    args = parser.parse_args()
    if not get_settings().seed_demo or not args.email.endswith("@sahyog.demo"):
        parser.error("Only available for explicitly enabled demo accounts")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.email))
        if not user or not user.totp_secret:
            parser.error("Demo user not found or TOTP not configured")
        print(
            pyotp.TOTP(user.totp_secret).provisioning_uri(
                user.email, issuer_name="SAHYOG Intelligence"
            )
            if args.provision
            else pyotp.TOTP(user.totp_secret).now()
        )

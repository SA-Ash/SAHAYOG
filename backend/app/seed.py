import secrets

import pyotp
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import passwords
from app.models.entities import Case, User
from app.schemas.cases import CaseCreate, TransactionInput
from app.services.cases import CaseService


def seed():
    settings = get_settings()
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.email == "sahyog-service@internal")):
            db.add(
                User(
                    name="SAHYOG Webhook Service",
                    email="sahyog-service@internal",
                    password_hash=passwords.hash(secrets.token_urlsafe(32)),
                    role="ADMIN",
                    org_unit="Integration",
                    is_active=True,
                )
            )
            db.commit()
        from app.services.public_labels import seed_public_labels

        seed_public_labels(db)
        if not settings.seed_demo:
            return
        for role, name in [
            ("INVESTIGATOR", "Aarav Sharma"),
            ("SUPERVISOR", "Meera Rao"),
            ("ADMIN", "System Administrator"),
        ]:
            email = f"{role.lower()}@sahyog.demo"
            if not db.scalar(select(User).where(User.email == email)):
                db.add(
                    User(
                        name=name,
                        email=email,
                        password_hash=passwords.hash(settings.demo_password),
                        role=role,
                        totp_secret=pyotp.random_base32(),
                        org_unit="Cyber Crime Unit · Delhi",
                    )
                )
        db.commit()
        from app.services.demo import seed_directory
        from app.services.probes import seed_probes

        seed_directory(db)
        seed_probes(db)
        actor = db.scalar(select(User).where(User.email == "investigator@sahyog.demo"))
        for i, (title, chain, token, amount, decimals) in enumerate(
            [
                ("Investment platform fraud", "tron", "USDT", "12500000000", 6),
                ("Impersonation & wallet transfer", "ethereum", "USDT", "4800000000", 6),
                ("Unverified transaction complaint", "bnb", None, None, None),
            ],
            start=1,
        ):
            ref = f"DEMO-2026-{i:04d}"
            if db.scalar(select(Case).where(Case.sahyog_ref == ref)):
                continue
            tx = TransactionInput(
                chain=chain, tx_hash=f"{i:064x}", token=token, amount=amount, decimals=decimals
            )
            CaseService.create_case(
                db,
                CaseCreate(title=title, sahyog_ref=ref, transaction=tx),
                actor,
                source="demo_seed",
            )
    print("Seed complete. Demo records are synthetic; existing users/passwords are unchanged.")


if __name__ == "__main__":
    seed()

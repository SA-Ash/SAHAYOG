import os

os.environ["JWT_SECRET"] = "test-only-jwt-secret-of-at-least-32-characters"
os.environ["SAHYOG_SERVICE_TOKEN"] = "test-only-service-token-at-least-24"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["MOCK_DATABASE_URL"] = "sqlite://"

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.core.security import passwords
from app.main import app
from app.models.entities import Base, User


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        for role in ["INVESTIGATOR", "SUPERVISOR", "ADMIN"]:
            session.add(
                User(
                    name=role,
                    email=f"{role.lower()}@test.org",
                    role=role,
                    password_hash=passwords.hash("Test-password-2026"),
                    totp_secret=pyotp.random_base32(),
                    org_unit="Test",
                )
            )
        session.add(
            User(
                name="Service",
                email="sahyog-service@internal",
                role="ADMIN",
                password_hash=passwords.hash("Unused-password"),
                org_unit="Integration",
            )
        )
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def sign_in(client, role="investigator"):
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    client.headers["X-CSRF-Token"] = token
    response = client.post(
        "/api/v1/auth/login", json={"email": f"{role}@test.org", "password": "Test-password-2026"}
    )
    assert response.status_code == 200, response.text
    return response

import hashlib
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from conftest import sign_in
from fastapi import Depends
from PIL import Image
from sqlalchemy import create_engine, inspect, select

from app.core.config import get_settings
from app.core.deps import require_elevated
from app.core.errors import AppError
from app.main import app
from app.models.entities import CaseAttachment, User
from app.services.extraction import extract_candidates
from app.services.validation import address_info, normalize_hash

PAYLOAD = {"title": "Victim complaint", "transaction": {"chain": "ethereum", "tx_hash": "a" * 64}}


@app.get("/test/elevated", dependencies=[Depends(require_elevated)])
def elevated_probe():
    return {"ok": True}


def test_auth_cookies_csrf_and_revocation(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "investigator@test.org", "password": "bad"}
        ).status_code
        == 403
    )
    response = sign_in(client)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "sahyog_session" not in response.json()
    assert client.get("/api/v1/auth/me").json()["role"] == "INVESTIGATOR"
    assert (
        client.post(
            "/api/v1/cases", json=PAYLOAD, headers={"Origin": "https://untrusted.test"}
        ).status_code
        == 403
    )
    session = client.cookies.get("sahyog_session")
    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    client.cookies.set("sahyog_session", session)
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("role", ["supervisor", "admin"])
def test_rbac(client, role):
    sign_in(client, role)
    assert client.get("/api/v1/cases").status_code == 200
    assert client.post("/api/v1/cases", json=PAYLOAD).status_code == 403
    assert (
        client.post(
            "/api/v1/cases/extract-from-image", files={"file": ("x.png", b"bad", "image/png")}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/cases/{uuid.uuid4()}/victim-transactions", json=PAYLOAD["transaction"]
        ).status_code
        == 403
    )


def test_case_intake_filters_precision_and_duplicate(client):
    sign_in(client)
    payload = {
        **PAYLOAD,
        "sahyog_ref": "TEST-001",
        "transaction": {
            **PAYLOAD["transaction"],
            "amount": "99999999999999999999999999999999999999",
            "decimals": 6,
            "token": "USDT",
        },
    }
    response = client.post("/api/v1/cases", json=payload)
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["transactions"][0]["status"] == "PENDING_RESOLUTION"
    assert case["transactions"][0]["amount"] == payload["transaction"]["amount"]
    assert case["transactions"][0]["tx_hash"] == "0x" + "a" * 64
    assert client.get("/api/v1/cases/" + case["id"]).json()["id"] == case["id"]
    assert (
        client.get("/api/v1/cases?chain=ethereum&status=OPEN&search=TEST-001").json()["total"] == 1
    )
    assert client.get("/api/v1/cases?chain=tron").json()["total"] == 0
    assert client.get("/api/v1/cases?date_from=2030-01-01&date_to=2020-01-01").status_code == 422
    today = datetime.now(timezone.utc).date().isoformat()
    assert client.get(f"/api/v1/cases?date_from={today}&date_to={today}").json()["total"] == 1
    assert client.post("/api/v1/cases", json=payload).status_code == 409
    path = "/api/v1/cases/" + case["id"] + "/victim-transactions"
    tx = {"chain": "tron", "tx_hash": "b" * 64}
    assert client.post(path, json=tx).status_code == 201
    assert client.post(path, json=tx).status_code == 409
    assert client.get("/api/v1/cases/" + str(uuid.uuid4())).status_code == 404


@pytest.mark.parametrize(
    "transaction",
    [
        {"chain": "ethereum"},
        {"chain": "ethereum", "tx_hash": "bad"},
        {"chain": "ethereum", "tx_hash": "a" * 64, "amount": 1.2},
        {"chain": "ethereum", "tx_hash": "a" * 64, "amount": "1"},
        {"chain": "ethereum", "tx_hash": "a" * 64, "amount": "0", "token": "ETH", "decimals": 18},
        {"chain": "ethereum", "tx_hash": "a" * 64, "tx_time": "2026-01-01T00:00:00"},
        {"chain": "tron", "suspect_address": "T" + "1" * 33},
    ],
)
def test_invalid_intake(client, transaction):
    sign_in(client)
    response = client.post("/api/v1/cases", json={**PAYLOAD, "transaction": transaction})
    assert response.status_code == 422
    assert set(response.json()) == {"code", "message", "details"}


def test_totp_enrollment_replay_and_session_binding(client, db):
    sign_in(client, "supervisor")
    assert client.get("/test/elevated").status_code == 403
    user = db.scalar(select(User).where(User.role == "SUPERVISOR"))
    user.totp_secret = None
    db.commit()
    setup = client.post("/api/v1/auth/2fa/setup", json={"password": "Test-password-2026"})
    assert setup.status_code == 200
    assert (
        client.post("/api/v1/auth/2fa/verify", json={"code": "123456"}).json()["code"]
        == "TOTP_NOT_CONFIGURED"
    )
    code = pyotp.TOTP(setup.json()["secret"]).now()
    response = client.post("/api/v1/auth/2fa/confirm", json={"code": code})
    assert response.status_code == 200, response.text
    assert client.get("/test/elevated").status_code == 200
    assert client.post("/api/v1/auth/2fa/verify", json={"code": code}).status_code == 409
    old_elevated = client.cookies.get("sahyog_elevated")
    sign_in(client, "supervisor")
    client.cookies.set("sahyog_elevated", old_elevated)
    assert client.get("/test/elevated").status_code == 403
    assert client.get("/api/v1/auth/me").json()["totp_enabled"] is True


def test_webhook_auth_and_idempotency(client):
    path = "/api/v1/integrations/sahyog/webhook"
    complaint = {
        "ref": "COMPLAINT-1",
        "title": "Victim complaint",
        "transaction": {**PAYLOAD["transaction"], "tx_time": "2026-01-01T00:00:00Z"},
    }
    assert client.post(path, json=complaint).status_code == 401
    headers = {"Authorization": "Bearer " + get_settings().sahyog_service_token}
    first = client.post(path, json=complaint, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["source"] == "simulated"
    assert client.post(path, json=complaint, headers=headers).json()["id"] == first.json()["id"]
    changed = {
        **complaint,
        "transaction": {**complaint["transaction"], "tx_time": "2026-01-02T00:00:00Z"},
    }
    assert client.post(path, json=changed, headers=headers).status_code == 409


def test_image_review_ownership_and_digest(client, db, tmp_path, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    monkeypatch.setattr(
        "app.services.extraction.extract_image",
        lambda raw: ({"candidates": [], "notices": [], "review_required": True}, b"sanitized PNG"),
    )
    response = client.post(
        "/api/v1/cases/extract-from-image",
        files={"file": ("image.png", b"original PNG", "image/png")},
    )
    assert response.status_code == 200
    image = response.json()
    assert image["sha256"] == hashlib.sha256(b"sanitized PNG").hexdigest()
    attachment = db.get(CaseAttachment, uuid.UUID(image["attachment_id"]))
    assert (
        attachment.extracted_json["original_sha256"] == hashlib.sha256(b"original PNG").hexdigest()
    )
    payload = {**PAYLOAD, "attachment_ids": [image["attachment_id"]]}
    assert client.post("/api/v1/cases", json=payload).json()["code"] == "REVIEW_REQUIRED"
    other = db.scalar(select(User).where(User.role == "ADMIN"))
    original_owner = attachment.uploaded_by
    attachment.uploaded_by = other.id
    db.commit()
    assert (
        client.get("/api/v1/cases/attachments/" + image["attachment_id"] + "/image").status_code
        == 404
    )
    assert (
        client.post("/api/v1/cases", json={**payload, "extraction_reviewed": True}).status_code
        == 403
    )
    attachment.uploaded_by = original_owner
    db.commit()
    assert (
        client.post("/api/v1/cases", json={**payload, "extraction_reviewed": True}).status_code
        == 201
    )
    assert (
        client.get("/api/v1/cases/attachments/" + image["attachment_id"] + "/image").content
        == b"sanitized PNG"
    )
    assert (
        client.post("/api/v1/cases", json={**payload, "extraction_reviewed": True}).status_code
        == 403
    )


def test_checksums_and_extraction():
    evm = "0x52908400098527886E0F7030069857D2E4169EE7"
    tron = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"
    btc = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
    assert address_info("ethereum", evm) == (evm.lower(), True)
    assert address_info("tron", tron)[1] is True
    assert address_info("bitcoin", btc)[1] is True
    with pytest.raises(ValueError):
        address_info("ethereum", "0x52908400098527886E0F7030069857D2E4169Ee7")
    with pytest.raises(ValueError):
        address_info("bitcoin", btc[:-1] + "1")
    assert normalize_hash("tron", "0x" + "A" * 64) == "a" * 64
    candidates = extract_candidates(
        f"{evm} {tron} {btc} " + "a" * 64 + " 1,200.50 USDT", 0.95, "ocr"
    )
    assert {c["type"] for c in candidates} == {
        "evm_address",
        "tron_address",
        "bitcoin_address",
        "tx_hash",
        "amount",
    }


def test_real_image_validation_and_qr():
    import cv2

    from app.services.extraction import extract_image

    with pytest.raises(AppError):
        extract_image(b"invalid")
    with pytest.raises(AppError):
        extract_image(b"x" * (5 * 1024 * 1024 + 1))
    output = io.BytesIO()
    qr = cv2.QRCodeEncoder_create().encode("0x" + "a" * 64)
    Image.fromarray(qr).resize((600, 600), Image.Resampling.NEAREST).save(output, format="PNG")
    result, stored = extract_image(output.getvalue())
    assert any(c["type"] == "tx_hash" and c["source"] == "qr" for c in result["candidates"])
    assert Image.open(io.BytesIO(stored)).format == "PNG"


def test_migration_upgrade_downgrade(tmp_path, monkeypatch):
    import app.core.db

    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    monkeypatch.setattr(app.core.db, "engine", engine)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    command.upgrade(config, "head")
    assert {"users", "cases", "victim_transactions", "case_attachments"} <= set(
        inspect(engine).get_table_names()
    )
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]

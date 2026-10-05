import pytest
from fastapi.testclient import TestClient
from mock_services.sahyog import main as portal
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import AppError
from app.services.sahyog import MockSahyogClient, RealSahyogClient


def test_mock_portal_contract(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(portal, "engine", engine)
    headers = {"Authorization": "Bearer test-only-service-token-at-least-24"}
    with TestClient(portal.app) as client:
        complaint = {
            "ref": "TEST/COMPLAINT-1",
            "title": "Victim complaint",
            "transaction": {"chain": "tron", "tx_hash": "a" * 64},
        }
        assert client.post("/sahyog/complaints", json=complaint).status_code == 401
        response = client.post("/sahyog/complaints", json=complaint, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["source"] == "simulated"
        assert (
            client.get("/sahyog/complaints/TEST/COMPLAPLAINT-1", headers=headers).status_code == 404
        )
        assert (
            client.get("/sahyog/complaints/TEST/COMPLAINT-1", headers=headers).json()[
                "transaction"
            ]["tx_hash"]
            == "a" * 64
        )
        assert (
            client.post(
                "/sahyog/complaints", json={**complaint, "title": "Changed title"}, headers=headers
            ).status_code
            == 409
        )
        request = client.post(
            "/sahyog/requests",
            json={
                "case_ref": "SHG-TEST",
                "kind": "FREEZE",
                "target_address": "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8",
                "outcome": "frozen",
            },
            headers=headers,
        )
        assert request.status_code == 201
        assert request.json()["state"] == "FROZEN"
        rid = request.json()["id"]
        assert client.get("/sahyog/requests/" + rid, headers=headers).json()["id"] == rid
    engine.dispose()


@pytest.mark.asyncio
async def test_real_client_is_explicit_stub():
    for call in [
        RealSahyogClient().get_complaint("REF"),
        RealSahyogClient().submit_request({}),
        RealSahyogClient().get_request("id"),
    ]:
        with pytest.raises(AppError, match="") as error:
            await call
        assert error.value.code == "INTEGRATION_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_mock_client_transport_failure(monkeypatch):
    import httpx

    async def failed(*args, **kwargs):
        raise httpx.ConnectError("Unavailable")

    monkeypatch.setattr(httpx.AsyncClient, "request", failed)
    with pytest.raises(AppError) as error:
        await MockSahyogClient().get_complaint("REF")
    assert error.value.code == "SAHYOG_UNAVAILABLE"


def test_mock_hold_idempotency_extension_release_and_expiry(monkeypatch):
    from datetime import timedelta

    from app.models.entities import now

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(portal, "engine", engine)
    headers = {"Authorization": "Bearer test-only-service-token-at-least-24"}
    with TestClient(portal.app) as client:
        payload = {
            "case_ref": "HOLD-TEST",
            "kind": "FREEZE",
            "target_address": "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8",
            "outcome": "frozen",
            "request_key": "hold-idempotent",
            "action": "HOLD",
            "expires_at": (now() + timedelta(minutes=1)).isoformat(),
        }
        first = client.post("/sahyog/requests", json=payload, headers=headers).json()
        assert (
            client.post("/sahyog/requests", json=payload, headers=headers).json()["id"]
            == first["id"]
        )
        deadline = (now() + timedelta(minutes=5)).isoformat()
        extended = client.post(
            "/sahyog/requests",
            json={
                **payload,
                "request_key": "hold-extend",
                "action": "EXTEND",
                "original_request_id": first["id"],
                "expires_at": deadline,
            },
            headers=headers,
        )
        assert extended.status_code == 201
        assert (
            client.get("/sahyog/requests/" + first["id"], headers=headers).json()["expires_at"]
            == deadline
        )
        assert (
            client.post(
                "/sahyog/requests",
                json={
                    **payload,
                    "request_key": "hold-release",
                    "action": "RELEASE",
                    "original_request_id": first["id"],
                },
                headers=headers,
            ).status_code
            == 201
        )
        assert (
            client.get("/sahyog/requests/" + first["id"], headers=headers).json()["state"]
            == "RELEASED"
        )
        expired = client.post(
            "/sahyog/requests",
            json={
                **payload,
                "request_key": "hold-expired",
                "expires_at": (now() - timedelta(seconds=1)).isoformat(),
            },
            headers=headers,
        ).json()
        assert (
            client.get("/sahyog/requests/" + expired["id"], headers=headers).json()["state"]
            == "EXPIRED"
        )
        assert (
            client.post(
                "/sahyog/requests",
                json={
                    **payload,
                    "request_key": "late-extension",
                    "action": "EXTEND",
                    "original_request_id": expired["id"],
                },
                headers=headers,
            ).status_code
            == 409
        )
    engine.dispose()

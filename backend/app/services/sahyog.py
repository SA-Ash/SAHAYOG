from abc import ABC, abstractmethod

import httpx

from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.cases import Complaint


class SahyogClient(ABC):
    @abstractmethod
    async def get_complaint(self, ref: str) -> Complaint: ...

    @abstractmethod
    async def submit_request(self, payload: dict) -> dict: ...

    @abstractmethod
    async def get_request(self, request_id: str) -> dict: ...


class MockSahyogClient(SahyogClient):
    async def _call(self, method, path, payload=None):
        settings = get_settings()
        try:
            async with httpx.AsyncClient(base_url=settings.sahyog_url, timeout=10) as client:
                response = await client.request(
                    method,
                    path,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.sahyog_service_token}"},
                )
                if response.status_code == 404:
                    raise AppError("COMPLAINT_NOT_FOUND", "SAHYOG record not found", 404)
                if 400 <= response.status_code < 500:
                    failure = response.json()
                    raise AppError(
                        failure.get("code", "SAHYOG_REJECTED"),
                        failure.get("message", "Portal rejected the request"),
                        response.status_code,
                    )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "SAHYOG_UNAVAILABLE", "SAHYOG could not be reached; retry later", 502
            ) from exc

    async def get_complaint(self, ref):
        from urllib.parse import quote

        return Complaint.model_validate(
            await self._call("GET", f"/sahyog/complaints/{quote(ref, safe='')}")
        )

    async def submit_request(self, payload):
        return await self._call("POST", "/sahyog/requests", payload)

    async def get_request(self, request_id):
        return await self._call("GET", f"/sahyog/requests/{request_id}")


class RealSahyogClient(SahyogClient):
    """Deliberately fail closed until an official contract and credentials exist."""

    async def get_complaint(self, ref):
        raise AppError(
            "INTEGRATION_NOT_CONFIGURED", "Real SAHYOG integration has not been implemented", 503
        )

    async def submit_request(self, payload):
        raise AppError(
            "INTEGRATION_NOT_CONFIGURED", "Real SAHYOG integration has not been implemented", 503
        )

    async def get_request(self, request_id):
        raise AppError(
            "INTEGRATION_NOT_CONFIGURED", "Real SAHYOG integration has not been implemented", 503
        )


def get_sahyog_client() -> SahyogClient:
    return MockSahyogClient() if get_settings().sahyog_mode == "mock" else RealSahyogClient()

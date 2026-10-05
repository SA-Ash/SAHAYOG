import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.errors import AppError, install_error_handlers
from app.schemas.cases import Complaint

engine = create_engine(os.getenv("MOCK_DATABASE_URL", "sqlite:///./mock_sahyog.db"), connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "portal_records"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


@asynccontextmanager
async def lifespan(_):
    # Independent mock service; its storage is deliberately not the case database.
    Base.metadata.create_all(engine)
    yield


def authenticated(authorization: str = Header(default="")):
    token = os.getenv("SAHYOG_SERVICE_TOKEN", "")
    if len(token) < 24:
        raise AppError("SERVICE_NOT_CONFIGURED", "Configure a service token", 503)
    if not secrets.compare_digest(authorization, "Bearer " + token):
        raise AppError("INVALID_SERVICE_TOKEN", "Service authentication required", 401)


def db_session():
    with Session(engine) as db:
        yield db


app = FastAPI(title="Mock SAHYOG Portal (simulated, not official API)", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)


@app.get("/health")
def health():
    return {"status": "ok", "source": "simulated"}


async def deliver(complaint: dict):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            result = await client.post(os.getenv("BACKEND_URL", "http://localhost:8000") + "/api/v1/integrations/sahyog/webhook", json=complaint, headers={"Authorization": "Bearer " + os.environ["SAHYOG_SERVICE_TOKEN"]})
            result.raise_for_status()
            return {"status": "delivered", "case_id": result.json()["id"]}
    except httpx.HTTPError:
        return {"status": "pending_retry", "message": "Complaint is saved; use the delivery endpoint to retry"}


@app.post("/sahyog/complaints", dependencies=[Depends(authenticated)], status_code=201)
async def create_complaint(data: Complaint, notify: bool = Query(False), db: Session = Depends(db_session)):
    payload = data.model_dump(mode="json")
    payload["source"] = "simulated"
    key = "complaint:" + data.ref
    record = db.get(Record, key)
    if record and record.payload != payload:
        raise AppError("REFERENCE_CONFLICT", "Complaint reference already exists", 409)
    if not record:
        db.add(Record(key=key, payload=payload))
        db.commit()
    return {**payload, "delivery": await deliver(payload) if notify else {"status": "not_requested"}}


@app.post("/sahyog/complaints/{ref:path}/deliver", dependencies=[Depends(authenticated)])
async def retry_delivery(ref: str, db: Session = Depends(db_session)):
    record = db.get(Record, "complaint:" + ref)
    if not record:
        raise AppError("NOT_FOUND", "Complaint not found", 404)
    return await deliver(record.payload)


@app.get("/sahyog/complaints/{ref:path}", response_model=Complaint, dependencies=[Depends(authenticated)])
def get_complaint(ref: str, db: Session = Depends(db_session)):
    record = db.get(Record, "complaint:" + ref)
    if not record:
        raise AppError("NOT_FOUND", "Complaint not found", 404)
    return record.payload


class RequestInput(BaseModel):
    case_ref: str = Field(min_length=3, max_length=80)
    kind: Literal["FREEZE", "DISCLOSURE", "BOTH"]
    target_address: str = Field(min_length=10, max_length=100)
    amount_by_case: dict[str, str] = Field(default_factory=dict)
    outcome: Literal["frozen", "declined", "delayed"] = "delayed"


@app.post("/sahyog/requests", status_code=201, dependencies=[Depends(authenticated)])
def create_request(data: RequestInput, db: Session = Depends(db_session)):
    rid = str(uuid.uuid4())
    payload = {"id": rid, **data.model_dump(), "state": {"frozen": "FROZEN", "declined": "DECLINED", "delayed": "ACKNOWLEDGED"}[data.outcome], "created_at": datetime.now(timezone.utc).isoformat(), "source": "simulated"}
    db.add(Record(key="request:" + rid, payload=payload))
    db.commit()
    return payload


@app.get("/sahyog/requests/{rid}", dependencies=[Depends(authenticated)])
def get_request(rid: uuid.UUID, db: Session = Depends(db_session)):
    record = db.get(Record, "request:" + str(rid))
    if not record:
        raise AppError("NOT_FOUND", "Request not found", 404)
    return record.payload

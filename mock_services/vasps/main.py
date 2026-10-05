import asyncio
import os
import secrets
import time
import uuid
from typing import Annotated
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import JSON, Float, String, create_engine, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.errors import AppError, install_error_handlers
from app.services.demo import DEMO_VASPS, demo_hub
from app.services.privacy import multiply, point, salted_hash, scalar


class Base(DeclarativeBase):
    pass


class PsiSession(Base):
    __tablename__ = "psi_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    vasp: Mapped[str] = mapped_column(String(40))
    owned_json: Mapped[list] = mapped_column(JSON)
    expires: Mapped[float] = mapped_column(Float)


engine = create_engine(
    os.getenv("VASP_DATABASE_URL", "sqlite:///./mock_vasps.db"),
    connect_args={"check_same_thread": False},
)


@asynccontextmanager
async def lifespan(_):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Simulated VASP Federation", version="0.7.0", lifespan=lifespan)
install_error_handlers(app)


def service(authorization: str = Header(default="")):
    token = os.getenv("SAHYOG_SERVICE_TOKEN", "")
    if len(token) < 24 or not secrets.compare_digest(authorization, "Bearer " + token):
        raise AppError("INVALID_SERVICE_TOKEN", "Service authentication required", 401)


def directory(name):
    entry = next((row for row in DEMO_VASPS if row[0] == name), None)
    if not entry:
        raise AppError("VASP_NOT_FOUND", "Unknown mock VASP", 404)
    return entry


async def delay(name):
    entry = directory(name)
    scale = float(os.getenv("MOCK_VASP_DELAY_SCALE", "1"))
    await asyncio.sleep((entry[4] + secrets.randbelow(100) / 1000) * scale)


def addresses(name):
    return [
        (chain, demo_hub(name, chain))
        for chain in ["ethereum", "bnb", "polygon", "tron", "bitcoin"]
    ]


Hex = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class HashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    salt: Hex
    hashes: list[Hex] = Field(min_length=1, max_length=100)
    query_id: uuid.UUID


class PsiInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[Hex] = Field(min_length=1, max_length=100)
    query_id: uuid.UUID


class PsiConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: uuid.UUID
    matched_points: list[Hex] = Field(max_length=100)


@app.get("/health")
def health():
    return {"status": "ok", "source": "simulated"}


@app.post("/vasp/{name}/lookup", dependencies=[Depends(service)])
async def lookup(name: str, data: HashInput):
    await delay(name)
    owned = {
        salted_hash(data.salt, chain, address) for chain, address in addresses(name)
    }
    matched = sorted(set(data.hashes) & owned)
    return {"match": bool(matched), "matched_hashes": matched, "source": "simulated"}


@app.post("/vasp/{name}/psi/round1", dependencies=[Depends(service)])
async def psi_round1(name: str, data: PsiInput):
    await delay(name)
    secret = scalar()
    try:
        evaluated = [multiply(secret, value) for value in data.points]
    except ValueError as exc:
        raise AppError("INVALID_PSI_POINT", "Invalid curve point", 422) from exc
    owned = [
        multiply(secret, point(chain, address)) for chain, address in addresses(name)
    ]
    session_id = str(uuid.uuid4())
    with Session(engine) as db:
        db.execute(delete(PsiSession).where(PsiSession.expires < time.time()))
        db.add(
            PsiSession(
                id=session_id, vasp=name, owned_json=owned, expires=time.time() + 60
            )
        )
        db.commit()
    return {
        "session_id": session_id,
        "evaluated": evaluated,
        "owned": owned,
        "source": "simulated",
    }


@app.post("/vasp/{name}/psi/round2", dependencies=[Depends(service)])
def psi_round2(name: str, data: PsiConfirm):
    directory(name)
    with Session(engine) as db:
        session = db.get(PsiSession, str(data.session_id))
        if not session or session.vasp != name or session.expires < time.time():
            raise AppError("PSI_SESSION_EXPIRED", "Start a new PSI round", 409)
        if not set(data.matched_points).issubset(set(session.owned_json)):
            raise AppError(
                "INVALID_PSI_MATCH", "Claimed point is outside the server set", 422
            )
        return {"match": bool(data.matched_points), "source": "simulated"}


@app.post("/federated/psi/round1", dependencies=[Depends(service)])
async def round1(data: PsiInput, name: str):
    return await psi_round1(name, data)


@app.post("/federated/psi/round2", dependencies=[Depends(service)])
def round2(data: PsiConfirm, name: str):
    return psi_round2(name, data)

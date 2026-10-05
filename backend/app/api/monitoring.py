import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import current_user, require_role
from app.core.errors import AppError
from app.core.security import check_csrf
from app.models.entities import Chain, User
from app.models.monitoring import Alert, BenchmarkRun, DormancyState, FenceMember, FenceProposal
from app.services.monitoring import MonitorService, serial
from app.services.validation import address_info

router = APIRouter(tags=["Wallet monitoring and hardening"])
mutation = [Depends(check_csrf)]
investigator = require_role("INVESTIGATOR")


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain: Chain
    address: str = Field(max_length=100)

    @model_validator(mode="after")
    def valid(self):
        self.address = address_info(self.chain, self.address)[0]
        return self


class DecisionInput(BaseModel):
    decision: Literal["ADD", "IGNORE", "VICTIM"]


class SettingsInput(BaseModel):
    threshold_days: int = Field(default=30, ge=1, le=3650)
    poll_seconds: int = Field(default=60, ge=10, le=86400)


class PollInput(BaseModel):
    as_of: datetime | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.as_of and self.as_of.utcoffset() is None:
            raise ValueError("Replay timestamp requires timezone")
        return self


@router.get("/cases/{case_id}/fence")
def fence(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = MonitorService.settings(db, case_id)
    return {
        "settings": serial(settings),
        "members": [
            serial(m) for m in db.scalars(select(FenceMember).where(FenceMember.case_id == case_id))
        ],
        "proposals": [
            serial(p)
            for p in db.scalars(select(FenceProposal).where(FenceProposal.case_id == case_id))
        ],
    }


@router.post("/cases/{case_id}/fence/members", dependencies=mutation)
def add_member(
    case_id: uuid.UUID,
    data: MemberInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return serial(MonitorService.add(db, case_id, data.chain.value, data.address, user))


@router.post("/cases/{case_id}/fence/proposals/{proposal_id}/decide", dependencies=mutation)
def decide(
    case_id: uuid.UUID,
    proposal_id: uuid.UUID,
    data: DecisionInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    settings = MonitorService.settings(db, case_id)
    row = db.get(FenceProposal, proposal_id)
    if not row or row.case_id != case_id:
        raise AppError("NOT_FOUND", "Proposal not found in this case", 404)
    if row.status != "PENDING":
        raise AppError("ALREADY_DECIDED", "Proposal already decided", 409)
    if data.decision == "ADD":
        MonitorService.add(db, case_id, row.chain, row.address, user, "proposal")
    if data.decision == "VICTIM":
        if row.direction != "in":
            raise AppError(
                "INVALID_VICTIM_TAG", "Only inbound proposals can be tagged as victims", 422
            )
        member = db.scalar(
            select(FenceMember).where(
                FenceMember.case_id == case_id,
                FenceMember.chain == row.chain,
                FenceMember.address == row.address,
            )
        )
        if member:
            db.delete(member)
        settings.victims_json = [*settings.victims_json, [row.chain, row.address]]
    row.status = data.decision
    db.commit()
    return serial(row)


@router.post("/cases/{case_id}/fence/poll", dependencies=mutation)
async def poll(
    case_id: uuid.UUID,
    data: PollInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return await MonitorService.poll(db, case_id, user, data.as_of)


@router.get("/alerts")
def alerts(
    case_id: uuid.UUID = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    MonitorService.settings(db, case_id)
    return [
        serial(a)
        for a in db.scalars(select(Alert).where(Alert.case_id == case_id).order_by(Alert.at.desc()))
    ]


@router.post("/alerts/{alert_id}/ack", dependencies=mutation)
def ack(alert_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Alert, alert_id)
    if not row:
        raise AppError("NOT_FOUND", "Alert not found", 404)
    if not row.ack_by:
        row.ack_by = user.id
        db.commit()
    return serial(row)


@router.get("/cases/{case_id}/dormancy")
def dormancy(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "settings": serial(MonitorService.settings(db, case_id)),
        "addresses": [
            serial(s)
            for s in db.scalars(select(DormancyState).where(DormancyState.case_id == case_id))
        ],
    }


@router.post("/cases/{case_id}/dormancy/threshold", dependencies=mutation)
def threshold(
    case_id: uuid.UUID,
    data: SettingsInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    row = MonitorService.settings(db, case_id)
    row.threshold_days, row.poll_seconds = data.threshold_days, data.poll_seconds
    for state in db.scalars(select(DormancyState).where(DormancyState.case_id == case_id)):
        state.threshold_days = data.threshold_days
    db.commit()
    return serial(row)


@router.get("/addresses/{chain}/{address}/activity")
async def activity(
    chain: str,
    address: str,
    case_id: uuid.UUID = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.adapters.registry import AdapterRegistry
    from app.services.cases import CaseService

    case = CaseService.get_case(db, case_id)
    try:
        address = address_info(chain, address)[0]
    except ValueError as exc:
        raise AppError("INVALID_ADDRESS", str(exc), 422) from exc
    return {
        "chain": chain,
        "address": address,
        "events": [
            t.model_dump(mode="json")
            for t in await AdapterRegistry(db, case.scenario_id)
            .get(chain)
            .get_transfers(address, "both")
        ],
    }


@router.get("/benchmarks")
def benchmarks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        serial(r)
        for r in db.scalars(select(BenchmarkRun).order_by(BenchmarkRun.at.desc()).limit(50))
    ]


@router.get("/version")
def version():
    return {"version": "0.16.0", "implemented_tasks": list(range(1, 16)), "partial_tasks": [16]}


@router.get("/health")
def health():
    from app.main import health as check

    return check()

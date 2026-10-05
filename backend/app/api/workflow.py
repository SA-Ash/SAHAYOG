import asyncio
import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import AuditService, audit
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.deps import current_user, require_elevated, require_role
from app.core.errors import AppError
from app.core.security import SESSION_COOKIE, check_csrf, decode_token
from app.models.entities import User, now
from app.models.intelligence import Attribution, RoutingDecision, TraceRun, Vasp
from app.models.workflow import (
    AuditLog,
    BlastRadiusReport,
    CaseCollision,
    Forecast,
    FreezeRequest,
    Notification,
    Report,
    TaintRun,
)
from app.schemas.cases import CaseOut
from app.schemas.workflow import (
    DraftInput,
    ForecastInput,
    GoldenInput,
    ReasonInput,
    ReportInput,
    SplitInput,
    TaintInput,
    TargetInput,
)
from app.services.attribution import attribution_json
from app.services.cases import CaseService
from app.services.forecast import ForecastService
from app.services.gangs import GangService
from app.services.impact import ImpactService
from app.services.reports import ReportService
from app.services.requests import RequestService, request_json
from app.services.taint import TaintService
from app.services.traces import graph_json

router = APIRouter(tags=["Investigation workflow"])
investigator = require_role("INVESTIGATOR")
admin = require_role("ADMIN")
reviewer = require_role("SUPERVISOR", "ADMIN")
mutation = [Depends(check_csrf)]


@router.post("/cases/{case_id}/taint", dependencies=mutation)
async def taint(
    case_id: uuid.UUID,
    data: TaintInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return await TaintService.run(db, case_id, data, user)


@router.get("/cases/{case_id}/taint/summary")
def taint_summary(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = TaintService.latest(db, case_id)
    return {"id": str(row.id), **row.result_json}


@router.get("/addresses/{chain}/{address}/taint")
def address_taint(
    chain: str,
    address: str,
    case_id: uuid.UUID = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.services.validation import address_info

    try:
        address = address_info(chain, address)[0]
    except ValueError as exc:
        raise AppError("INVALID_ADDRESS", str(exc), 422) from exc
    run = TaintService.latest(db, case_id)
    return {
        "run_id": str(run.id),
        "method": run.method,
        "balances": [
            b
            for b in run.result_json["balances"]
            if b["chain"] == chain and b["address"] == address
        ],
        "evidence": run.result_json["evidence"],
    }


@router.get("/gang-cases/{gang_id}")
def gang(gang_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return GangService.detail(db, gang_id)


@router.post("/gang-cases/{gang_id}/split-preview", dependencies=mutation)
def split(
    gang_id: uuid.UUID,
    data: SplitInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    result = GangService.split(db, gang_id, data.available_amount)
    audit(user.id, "gang.split_preview", {"gang_id": str(gang_id)}, db=db)
    return result


@router.get("/gang-cases/{gang_id}/communities")
def communities(
    gang_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return GangService.communities(db, gang_id)


@router.get("/cases/{case_id}/wallet-farms")
async def farms(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return await GangService.farms(db, case_id)


@router.post("/wallet-farms/{farm_id}/approve", dependencies=mutation)
def approve_farm(
    farm_id: uuid.UUID, user: User = Depends(investigator), db: Session = Depends(get_db)
):
    return GangService.approve_farm(db, farm_id, user)


@router.get("/cases/{case_id}/similar-operators")
def similar(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return GangService.similar(db, case_id)


@router.get("/collisions")
def collisions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return GangService.collisions(db, user)


@router.post("/collisions/{collision_id}/open-room", dependencies=mutation)
def open_room(
    collision_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return GangService.open_room(db, collision_id, user)


@router.get("/collisions/{collision_id}/room")
def room(
    collision_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    visible = next(
        (c for c in GangService.collisions(db, user) if c["id"] == str(collision_id)), None
    )
    if not visible:
        raise AppError("FORBIDDEN", "Participating units only", 403)
    return visible["room"]


@router.post("/collisions/{collision_id}/room", dependencies=mutation)
def room_message(
    collision_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    current = room(collision_id, user, db)
    if not current:
        raise AppError("ROOM_NOT_OPEN", "Open the coordination room first", 409)
    row = db.get(CaseCollision, collision_id)
    row.room_json = {
        **current,
        "messages": [
            *current["messages"],
            {
                "id": str(uuid.uuid4()),
                "unit": user.org_unit,
                "author": user.name,
                "text": data.reason,
                "at": now().isoformat(),
            },
        ],
    }
    audit(user.id, "collision.room_message", {"collision_id": str(collision_id)}, db=db)
    return row.room_json


@router.post("/cases/{case_id}/blast-radius", dependencies=mutation)
async def impact(
    case_id: uuid.UUID,
    data: TargetInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return await ImpactService.compute(db, case_id, data, user)


@router.get("/cases/{case_id}/blast-radius")
def latest_impact(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = ImpactService.latest(db, case_id)
    return {"id": str(row.id), **row.result_json}


@router.post("/cases/{case_id}/requests", dependencies=mutation, status_code=201)
def draft(
    case_id: uuid.UUID,
    data: DraftInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return request_json(db, RequestService.draft(db, case_id, data, user))


@router.get("/cases/{case_id}/requests")
def case_requests(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    CaseService.get_case(db, case_id)
    return [
        request_json(db, row)
        for row in db.scalars(
            select(FreezeRequest)
            .where(FreezeRequest.case_id == case_id)
            .order_by(FreezeRequest.created_at.desc())
        )
    ]


@router.get("/requests/{request_id}")
async def request_detail(
    request_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    await RequestService.expire(db)
    return request_json(db, RequestService.get(db, request_id))


def act(db, request_id, action, data, user, elevated=False):
    row = RequestService.act(
        db, request_id, action, user, data.reason, elevated, getattr(data, "minutes", 30)
    )
    if action in {"approve", "golden-hour", "release", "confirm"}:
        job = RequestService.queue(
            db,
            row,
            "release"
            if action == "release"
            else "extend"
            if action == "confirm" and row.sahyog_request_id
            else "send",
        )
        return {**request_json(db, row), "job_id": str(job.id)}
    return request_json(db, row)


@router.post("/requests/{request_id}/propose", dependencies=mutation)
def propose(
    request_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return act(db, request_id, "propose", data, user)


@router.post("/requests/{request_id}/approve", dependencies=mutation)
async def approve(
    request_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(require_elevated),
    db: Session = Depends(get_db),
):
    await RequestService.preflight(db, RequestService.get(db, request_id))
    return act(db, request_id, "approve", data, user, True)


@router.post("/requests/{request_id}/reject", dependencies=mutation)
def reject(
    request_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(reviewer),
    db: Session = Depends(get_db),
):
    return act(db, request_id, "reject", data, user)


@router.post("/requests/{request_id}/return", dependencies=mutation)
def return_request(
    request_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(reviewer),
    db: Session = Depends(get_db),
):
    return act(db, request_id, "return", data, user)


@router.post("/requests/{request_id}/golden-hour", dependencies=mutation)
async def golden(
    request_id: uuid.UUID,
    data: GoldenInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    await RequestService.preflight(db, RequestService.get(db, request_id))
    return act(db, request_id, "golden-hour", data, user)


@router.post("/requests/{request_id}/confirm", dependencies=mutation)
async def confirm(
    request_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(require_elevated),
    db: Session = Depends(get_db),
):
    await RequestService.expire(db)
    return act(db, request_id, "confirm", data, user, True)


@router.post("/requests/{request_id}/release", dependencies=mutation)
def release(
    request_id: uuid.UUID,
    data: ReasonInput,
    user: User = Depends(require_elevated),
    db: Session = Depends(get_db),
):
    return act(db, request_id, "release", data, user, True)


@router.post("/requests/{request_id}/retry", dependencies=mutation)
def retry(request_id: uuid.UUID, user: User = Depends(investigator), db: Session = Depends(get_db)):
    row = RequestService.get(db, request_id)
    if (
        row.created_by != user.id
        or row.state not in {"APPROVED", "SENT", "ACKNOWLEDGED", "FROZEN", "RELEASED", "EXPIRED"}
        or not row.dispatch_error
    ):
        raise AppError("RETRY_NOT_ALLOWED", "Only failed authorized dispatches may be retried", 409)
    job = RequestService.queue(
        db,
        row,
        row.dispatch_error.get(
            "operation", "release" if row.state in {"RELEASED", "EXPIRED"} else "send"
        ),
    )
    return {"job_id": str(job.id)}


@router.post("/requests/{request_id}/poll", dependencies=mutation)
async def poll(
    request_id: uuid.UUID, user: User = Depends(reviewer), db: Session = Depends(get_db)
):
    row = await RequestService.poll(db, RequestService.get(db, request_id))
    return request_json(db, row)


@router.get("/audit")
def audit_entries(
    entity: str | None = None,
    id: str | None = None,
    user: User = Depends(reviewer),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if id:
        stmt = stmt.where(AuditLog.entity_id == id)
    return [
        {
            "id": r.id,
            "actor_id": r.actor_id,
            "action": r.action,
            "entity": r.entity,
            "entity_id": r.entity_id,
            "payload": r.payload_json,
            "prev_hash": r.prev_hash,
            "entry_hash": r.entry_hash,
            "at": r.at,
        }
        for r in db.scalars(stmt.order_by(AuditLog.id.desc()).limit(500))
    ]


@router.get("/audit/verify")
def verify_audit(user: User = Depends(admin), db: Session = Depends(get_db)):
    return AuditService.verify(db)


@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        {
            "id": r.id,
            "request_id": r.request_id,
            "kind": r.kind,
            "payload": r.payload_json,
            "read_at": r.read_at,
            "at": r.created_at,
        }
        for r in db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.id.desc())
            .limit(100)
        )
    ]


@router.post("/notifications/{notification_id}/read", dependencies=mutation)
def read_notification(
    notification_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(Notification, notification_id)
    if not row or row.user_id != user.id:
        raise AppError("NOT_FOUND", "Notification not found", 404)
    row.read_at = now()
    audit(user.id, "notification.read", {"notification_id": notification_id}, db=db)
    return {"read": True}


@router.websocket("/ws/notifications")
async def notification_events(websocket: WebSocket, after: int = 0):
    if websocket.headers.get("origin") not in {None, get_settings().frontend_origin}:
        await websocket.close(code=4403)
        return
    try:
        claims = decode_token(websocket.cookies.get(SESSION_COOKIE, ""))
        user_id = uuid.UUID(claims["sub"])
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if not user or not user.is_active or user.session_version != claims["ver"]:
                raise AppError("UNAUTHENTICATED", "Session revoked", 401)
        await websocket.accept()
        cursor = max(0, after)
        while True:
            decode_token(websocket.cookies.get(SESSION_COOKIE, ""))
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if not user or not user.is_active or user.session_version != claims["ver"]:
                    await websocket.close(code=4401)
                    return
                rows = list(
                    db.scalars(
                        select(Notification)
                        .where(Notification.user_id == user_id, Notification.id > cursor)
                        .order_by(Notification.id)
                        .limit(100)
                    )
                )
                for row in rows:
                    await websocket.send_json(
                        {
                            "id": row.id,
                            "kind": row.kind,
                            "request_id": str(row.request_id) if row.request_id else None,
                            "payload": row.payload_json,
                        }
                    )
                    cursor = row.id
            await websocket.send_json({"type": "heartbeat"})
            await asyncio.sleep(1)
    except (AppError, ValueError):
        await websocket.close(code=4401)
    except (WebSocketDisconnect, RuntimeError):
        return


@router.post("/cases/{case_id}/reports", dependencies=mutation, status_code=201)
def build_report(
    case_id: uuid.UUID,
    data: ReportInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return ReportService.build(db, case_id, data, user)


@router.get("/cases/{case_id}/reports")
def reports(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    CaseService.get_case(db, case_id)
    return [
        {
            "id": str(r.id),
            "version": r.version,
            "bundle_hash": r.bundle_hash,
            "merkle_root": r.merkle_root,
            "created_at": r.created_at,
            "items": [
                {"id": item["id"], "kind": item["kind"]} for item in r.bundle_json["evidence_items"]
            ],
        }
        for r in db.scalars(
            select(Report).where(Report.case_id == case_id).order_by(Report.version.desc())
        )
    ]


def sealed_report(db, report_id):
    row = db.get(Report, report_id)
    if not row:
        raise AppError("REPORT_NOT_FOUND", "Report not found", 404)
    if not ReportService.verify(db, row.bundle_hash)["valid"]:
        raise AppError("REPORT_TAMPERED", "Stored evidence failed integrity verification", 409)
    return row


@router.get("/reports/{report_id}/pdf")
def pdf(report_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = sealed_report(db, report_id)
    path = Path(row.pdf_path).resolve()
    if (
        path.parent != ReportService.directory().resolve()
        or not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != row.pdf_hash
    ):
        raise AppError("PDF_INTEGRITY_FAILED", "PDF is missing or changed", 409)
    return FileResponse(
        path, media_type="application/pdf", filename=f"SAHYOG-{report_id}-v{row.version}.pdf"
    )


@router.get("/reports/{report_id}/bundle.json")
def bundle(report_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return sealed_report(db, report_id).bundle_json


@router.get("/reports/{report_id}/proof/{item_id}")
def proof(
    report_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    sealed_report(db, report_id)
    return ReportService.proof(db, report_id, item_id)


@router.get("/verify/{bundle_hash}")
def public_verify(bundle_hash: str, db: Session = Depends(get_db)):
    return ReportService.verify(db, bundle_hash)


@router.post("/verify/{bundle_hash}")
def verify_uploaded(bundle_hash: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    return ReportService.verify(db, bundle_hash, payload)


@router.get("/analytics/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return ReportService.overview(db)


@router.get("/analytics/vasps")
def vasp_analytics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services.routing import vasp_json

    return [vasp_json(v) for v in db.scalars(select(Vasp))]


@router.post("/dev/forecast/train", dependencies=mutation)
def train_forecast(user: User = Depends(admin), db: Session = Depends(get_db)):
    if not get_settings().dev_tools_enabled:
        raise AppError("DEV_TOOLS_DISABLED", "Training tools disabled", 404)
    return ForecastService.train(db, user)


@router.post("/cases/{case_id}/forecast", dependencies=mutation)
def forecast(
    case_id: uuid.UUID,
    data: ForecastInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return ForecastService.run(db, case_id, data, user)


@router.get("/cases/{case_id}/forecast")
def latest_forecast(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = ForecastService.latest(db, case_id)
    return {"id": str(row.id), **row.result_json}


@router.post("/cases/{case_id}/forecast/prestage", dependencies=mutation)
def prestage(case_id: uuid.UUID, user: User = Depends(investigator), db: Session = Depends(get_db)):
    return ForecastService.prestage(db, case_id, user)


@router.get("/cases/{case_id}/workspace")
def workspace(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    case = CaseService.get_case(db, case_id)
    trace = db.scalar(
        select(TraceRun).where(TraceRun.case_id == case_id).order_by(TraceRun.started_at.desc())
    )
    attribution = (
        db.scalar(select(Attribution).where(Attribution.trace_run_id == trace.id))
        if trace
        else None
    )
    taint = db.scalar(
        select(TaintRun).where(TaintRun.case_id == case_id).order_by(TaintRun.created_at.desc())
    )
    impact = db.scalar(
        select(BlastRadiusReport)
        .where(BlastRadiusReport.case_id == case_id)
        .order_by(BlastRadiusReport.created_at.desc())
    )
    forecast = db.scalar(
        select(Forecast).where(Forecast.case_id == case_id).order_by(Forecast.created_at.desc())
    )
    routing = db.scalar(
        select(RoutingDecision)
        .where(RoutingDecision.case_id == case_id)
        .order_by(RoutingDecision.created_at.desc())
    )
    return {
        "case": CaseOut.model_validate(case).model_dump(mode="json"),
        "graph": graph_json(db, trace) if trace else None,
        "attribution": attribution_json(db, attribution) if attribution else None,
        "taint": {
            **taint.result_json,
            "id": str(taint.id),
            "stale": bool(trace and taint.trace_run_id != trace.id),
        }
        if taint
        else None,
        "impact": {
            **impact.result_json,
            "id": str(impact.id),
            "stale": bool(taint and impact.taint_run_id != taint.id),
        }
        if impact
        else None,
        "requests": case_requests(case_id, user, db),
        "forecast": forecast.result_json if forecast else None,
        "routing": {
            "channel": routing.channel,
            "issuer_target": routing.issuer_target,
            "evidence": routing.reason_json,
        }
        if routing
        else None,
        "reports": reports(case_id, user, db),
    }

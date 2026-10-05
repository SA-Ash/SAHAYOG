import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.http import replay_enabled
from app.adapters.registry import AdapterRegistry
from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.deps import current_user, require_role
from app.core.errors import AppError
from app.core.security import SESSION_COOKIE, check_csrf, decode_token
from app.models.entities import User
from app.models.intelligence import (
    CaseEvent,
    Cluster,
    ClusterMember,
    Entity,
    FederatedReply,
    GraphNode,
    Job,
    PatternFinding,
    ProbeRun,
    Scenario,
    ScenarioTruth,
    Setting,
    Vasp,
)
from app.schemas.cases import CaseOut
from app.schemas.intelligence import (
    LabelInput,
    LookupInput,
    ProbeInput,
    ReplayInput,
    ScenarioInput,
    SwapServiceInput,
    TraceParams,
)
from app.services.attribution import AttributionService, attribution_json
from app.services.cases import CaseService
from app.services.federated import FederatedLookupService
from app.services.jobs import dispatch, job_json
from app.services.labels import LabelService, label_json
from app.services.probes import ProbeService
from app.services.routing import RoutingService, vasp_json
from app.services.scenarios import ScenarioGenerator
from app.services.traces import TraceService, graph_json, latest_trace

router = APIRouter(tags=["Intelligence"])
admin = require_role("ADMIN")
investigator = require_role("INVESTIGATOR")
mutation = [Depends(check_csrf)]


def dev_enabled():
    if not get_settings().dev_tools_enabled:
        raise AppError("DEV_TOOLS_DISABLED", "Scenario tools are disabled", 404)


def validate_window(start, end):
    if any(value and value.utcoffset() is None for value in [start, end]) or (
        start and end and start > end
    ):
        raise AppError("INVALID_TIME_WINDOW", "Use ordered timestamps with timezones", 422)


def adapter(db, chain, scenario_id):
    if scenario_id and not db.get(Scenario, scenario_id):
        raise AppError("SCENARIO_NOT_FOUND", "Scenario not found", 404)
    return AdapterRegistry(db, scenario_id).get(chain)


@router.get("/chains")
def chains(user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    replay = replay_enabled(db)
    return {
        "replay_mode": replay,
        "chains": [
            {
                "chain": chain,
                "implemented": True,
                "status": "replay"
                if replay
                else "configured"
                if chain in {"tron", "bitcoin"} or settings.etherscan_api_key
                else "credentials_required",
                "provider": "TronGrid"
                if chain == "tron"
                else "Esplora"
                if chain == "bitcoin"
                else "Etherscan V2",
                "scope": "UTXO outputs" if chain == "bitcoin" else "token and native transfers",
            }
            for chain in ["tron", "ethereum", "bnb", "polygon", "bitcoin"]
        ],
        "planned": ["solana"],
        "evidence": [
            {
                "signal": "adapter_configuration",
                "note": "Configuration state; provider availability is checked on requests",
            }
        ],
    }


@router.post("/admin/replay-mode", dependencies=mutation)
def replay(data: ReplayInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = db.get(Setting, "replay_mode")
    if not row:
        row = Setting(key="replay_mode", value={})
        db.add(row)
    row.value = {"enabled": data.enabled}
    db.commit()
    audit(user.id, "replay.changed", row.value, db=db)
    return {"enabled": data.enabled}


@router.get("/chain/{chain}/tx/{tx_hash}")
async def tx_lookup(
    chain: str,
    tx_hash: str,
    scenario_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    audit(user.id, "chain.tx_read", {"chain": chain, "tx_hash": tx_hash}, db=db)
    try:
        return {"transfers": await adapter(db, chain, scenario_id).get_tx(tx_hash)}
    except ValueError as exc:
        raise AppError("INVALID_CHAIN_INPUT", str(exc), 422) from exc


@router.get("/chain/{chain}/address/{address}/transfers")
async def transfers(
    chain: str,
    address: str,
    direction: str = Query("out", pattern="^(in|out|both)$"),
    time_from: datetime | None = Query(None, alias="from"),
    time_to: datetime | None = Query(None, alias="to"),
    scenario_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    validate_window(time_from, time_to)
    audit(user.id, "chain.transfers_read", {"chain": chain, "address": address}, db=db)
    try:
        return {
            "transfers": await adapter(db, chain, scenario_id).get_transfers(
                address, direction, time_from, time_to
            )
        }
    except ValueError as exc:
        raise AppError("INVALID_CHAIN_INPUT", str(exc), 422) from exc


@router.get("/chain/{chain}/address/{address}/profile")
async def profile(
    chain: str,
    address: str,
    scenario_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    audit(user.id, "chain.profile_read", {"chain": chain, "address": address}, db=db)
    try:
        return await adapter(db, chain, scenario_id).get_profile(address)
    except ValueError as exc:
        raise AppError("INVALID_CHAIN_INPUT", str(exc), 422) from exc


@router.get("/chain/{chain}/address/{address}/balance")
async def balance(
    chain: str,
    address: str,
    token: str | None = None,
    scenario_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    audit(user.id, "chain.balance_read", {"chain": chain, "address": address}, db=db)
    try:
        return await adapter(db, chain, scenario_id).get_balance(address, token)
    except ValueError as exc:
        raise AppError("INVALID_CHAIN_INPUT", str(exc), 422) from exc


@router.get("/dev/scenarios", dependencies=[Depends(dev_enabled)])
def scenarios(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "seed": s.seed,
            "params": s.params_json,
            "created_at": s.created_at,
            "source": "simulated",
        }
        for s in db.scalars(select(Scenario).order_by(Scenario.created_at.desc()))
    ]


@router.post("/dev/scenarios", dependencies=[*mutation, Depends(dev_enabled)], status_code=201)
def generate(
    data: ScenarioInput,
    user: User = Depends(require_role("ADMIN", "INVESTIGATOR")),
    db: Session = Depends(get_db),
):
    row = ScenarioGenerator.generate(db, data)
    audit(user.id, "scenario.generated", {"scenario_id": str(row.id)}, db=db)
    return {
        "id": str(row.id),
        "name": row.name,
        "seed": row.seed,
        "params": row.params_json,
        "source": "simulated",
    }


@router.get("/dev/scenarios/{scenario_id}/truth", dependencies=[Depends(dev_enabled)])
def truth(
    scenario_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(ScenarioTruth, scenario_id)
    if not row:
        raise AppError("SCENARIO_NOT_FOUND", "Scenario not found", 404)
    audit(user.id, "scenario.truth_read", {"scenario_id": str(scenario_id)}, db=db)
    return row.truth_json


@router.post(
    "/dev/scenarios/{scenario_id}/load-as-case",
    response_model=list[CaseOut],
    dependencies=[*mutation, Depends(dev_enabled)],
)
def load(scenario_id: uuid.UUID, user: User = Depends(investigator), db: Session = Depends(get_db)):
    row = db.get(Scenario, scenario_id)
    if not row:
        raise AppError("SCENARIO_NOT_FOUND", "Scenario not found", 404)
    return ScenarioGenerator.load(db, row, user)


@router.post("/cases/{case_id}/trace", dependencies=mutation, status_code=202)
async def trace(
    case_id: uuid.UUID,
    data: TraceParams,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    row = TraceService.start(db, case_id, data, user)
    if row.status == "QUEUED":
        dispatch(row.job_id)
    return {"trace_run_id": str(row.id), "job_id": str(row.job_id), "status": row.status}


@router.get("/cases/{case_id}/trace")
@router.get("/cases/{case_id}/graph")
def graph(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    CaseService.get_case(db, case_id)
    audit(user.id, "case.graph_read", {"case_id": str(case_id)}, db=db)
    return graph_json(db, latest_trace(db, case_id))


@router.get("/jobs/{job_id}")
def job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Job, job_id)
    if not row:
        raise AppError("JOB_NOT_FOUND", "Job not found", 404)
    audit(user.id, "job.read", {"job_id": str(job_id)}, db=db)
    return job_json(row)


@router.get("/cases/{case_id}/patterns")
def patterns(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    CaseService.get_case(db, case_id)
    run = latest_trace(db, case_id, completed=True)
    return [
        {
            "id": str(p.id),
            "kind": p.kind,
            "nodes": p.node_ids_json,
            "score": p.score,
            "evidence": p.evidence_json,
        }
        for p in db.scalars(select(PatternFinding).where(PatternFinding.trace_run_id == run.id))
    ]


@router.post("/cases/{case_id}/attribute", dependencies=mutation)
async def attribute(
    case_id: uuid.UUID, user: User = Depends(investigator), db: Session = Depends(get_db)
):
    row = await AttributionService.run(db, case_id, user)
    if row.confidence < 0.6:
        query = FederatedLookupService.start(db, case_id, "hash", user)
        dispatch(query.job_id)
    return attribution_json(db, row)


@router.get("/cases/{case_id}/attribution")
def attribution(
    case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    CaseService.get_case(db, case_id)
    audit(user.id, "attribution.read", {"case_id": str(case_id)}, db=db)
    return attribution_json(db, AttributionService.latest(db, case_id))


@router.get("/entities")
def entities(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        {"id": str(e.id), "name": e.name, "type": e.type, "country": e.country, "source": e.source}
        for e in db.scalars(select(Entity).order_by(Entity.name))
    ]


@router.get("/labels")
def labels(
    chain: str,
    address: str,
    scenario_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    adapter(db, chain, scenario_id)
    try:
        return [label_json(db, row) for row in LabelService.lookup(db, chain, address, scenario_id)]
    except ValueError as exc:
        raise AppError("INVALID_ADDRESS", str(exc), 422) from exc


@router.post("/labels", dependencies=mutation, status_code=201)
def add_label(data: LabelInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = LabelService.add(db, data, user)
    audit(user.id, "label.added", {"label_id": str(row.id)}, db=db)
    return label_json(db, row)


@router.post("/labels/import", dependencies=mutation)
def import_labels(
    file: UploadFile = File(...), user: User = Depends(admin), db: Session = Depends(get_db)
):
    raw = file.file.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", "CSV limit is 1 MB", 413)
    rows = LabelService.import_csv(db, raw, user)
    audit(user.id, "label.imported", {"count": len(rows)}, db=db)
    return {"imported": len(rows)}


@router.get("/clusters/{cluster_id}")
def cluster(
    cluster_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(Cluster, cluster_id)
    if not row:
        raise AppError("CLUSTER_NOT_FOUND", "Cluster not found", 404)
    entity = db.get(Entity, row.entity_id) if row.entity_id else None
    return {
        "id": str(row.id),
        "chain": row.chain,
        "entity": entity.name if entity else None,
        "kind": row.kind,
        "members": [
            {"address": m.address, "evidence": m.evidence}
            for m in db.scalars(select(ClusterMember).where(ClusterMember.cluster_id == row.id))
        ],
    }


@router.get("/nodes/{node_id}/why")
async def why(
    node_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(GraphNode, node_id)
    if not row:
        raise AppError("NODE_NOT_FOUND", "Node not found", 404)
    from app.models.intelligence import TraceRun

    case = CaseService.get_case(db, db.get(TraceRun, row.trace_run_id).case_id)
    adapter_instance = adapter(db, row.chain, case.scenario_id)
    profile_data = await adapter_instance.get_profile(row.address)
    return {
        "id": str(row.id),
        "chain": row.chain,
        "address": row.address,
        "hop": row.hop,
        "role": row.role,
        "evidence": row.evidence_json,
        "labels": [
            label_json(db, label)
            for label in LabelService.lookup(db, row.chain, row.address, case.scenario_id)
        ],
        "profile": profile_data,
        "cluster_id": str(row.cluster_id) if row.cluster_id else None,
    }


@router.get("/nodes/{node_id}/mixer-score")
async def mixer(
    node_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(GraphNode, node_id)
    if not row:
        raise AppError("NODE_NOT_FOUND", "Node not found", 404)
    from app.engines.signals import mixer_score
    from app.models.intelligence import TraceRun

    case = CaseService.get_case(db, db.get(TraceRun, row.trace_run_id).case_id)
    source = adapter(db, row.chain, case.scenario_id)
    incoming, outgoing = (
        await source.get_transfers(row.address, "in"),
        await source.get_transfers(row.address, "out"),
    )
    return mixer_score(
        incoming, outgoing, {t.to_addr: await source.get_profile(t.to_addr) for t in outgoing[:30]}
    )


@router.post("/dev/calibration/train", dependencies=[*mutation, Depends(dev_enabled)])
def calibration(user: User = Depends(admin), db: Session = Depends(get_db)):
    return AttributionService.train_calibration(db)


@router.get("/probes/exchanges")
def probe_exchanges(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return ProbeService.exchanges(db)


@router.get("/probes/exchanges/{entity_id}/hotwallets")
def hotwallets(
    entity_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if not db.get(Entity, entity_id):
        raise AppError("ENTITY_NOT_FOUND", "Entity not found", 404)
    from app.models.intelligence import HotwalletCluster

    return {
        "clusters": [
            ProbeService.cluster_json(db, c)
            for c in db.scalars(
                select(HotwalletCluster).where(HotwalletCluster.entity_id == entity_id)
            )
        ],
        "probes": [
            {
                "id": str(p.id),
                "chain": p.chain,
                "deposit_address": p.deposit_address,
                "amount": p.amount,
                "swept_to": p.swept_to,
                "sweep_delay_s": p.sweep_delay_s,
                "run_at": p.run_at,
                "source": p.mode,
            }
            for p in db.scalars(
                select(ProbeRun)
                .where(ProbeRun.entity_id == entity_id)
                .order_by(ProbeRun.run_at.desc())
                .limit(100)
            )
        ],
    }


@router.get("/probes/match")
def match(
    chain: str, address: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    adapter(db, chain, None)
    try:
        return {"matches": ProbeService.match_hub(db, chain, address)}
    except ValueError as exc:
        raise AppError("INVALID_ADDRESS", str(exc), 422) from exc


@router.post("/probes/run", dependencies=mutation)
def simulate_probe(data: ProbeInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    return ProbeService.simulate(db, data, user)


@router.post("/probes/import", dependencies=mutation)
def import_probes(
    file: UploadFile = File(...), user: User = Depends(admin), db: Session = Depends(get_db)
):
    raw = file.file.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", "CSV limit is 1 MB", 413)
    return ProbeService.import_csv(db, raw, user)


@router.post("/cases/{case_id}/federated-lookup", dependencies=mutation, status_code=202)
async def federated(
    case_id: uuid.UUID,
    data: LookupInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    query = FederatedLookupService.start(db, case_id, data.mode, user)
    if db.get(Job, query.job_id).status == "QUEUED":
        dispatch(query.job_id)
    return {"query_id": str(query.id), "job_id": str(query.job_id), "mode": query.mode}


@router.get("/cases/{case_id}/federated-lookup")
def replies(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    CaseService.get_case(db, case_id)
    audit(user.id, "federated.read", {"case_id": str(case_id)}, db=db)
    return FederatedLookupService.latest(db, case_id)


@router.get("/vasps")
def vasps(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        vasp_json(row) for row in db.scalars(select(Vasp).order_by(Vasp.responsiveness_ewma.desc()))
    ]


@router.get("/vasps/{vasp_id}/stats")
def stats(vasp_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Vasp, vasp_id)
    if not row:
        raise AppError("VASP_NOT_FOUND", "VASP not found", 404)
    return {
        **vasp_json(row),
        "history": [
            {"status": r.status, "response_s": r.response_s, "received_at": r.received_at}
            for r in db.scalars(
                select(FederatedReply)
                .where(FederatedReply.vasp_id == vasp_id)
                .order_by(FederatedReply.received_at.desc())
                .limit(30)
            )
        ],
    }


@router.post("/cases/{case_id}/route", dependencies=mutation)
def route(case_id: uuid.UUID, user: User = Depends(investigator), db: Session = Depends(get_db)):
    return RoutingService.recommend(db, case_id, user)


@router.websocket("/ws/cases/{case_id}")
async def events(websocket: WebSocket, case_id: uuid.UUID, after: int = 0):
    origin = websocket.headers.get("origin")
    if origin and origin != get_settings().frontend_origin:
        await websocket.close(code=4403)
        return
    try:
        claims = decode_token(websocket.cookies.get(SESSION_COOKIE, ""))
        with SessionLocal() as db:
            user = db.get(User, uuid.UUID(claims["sub"]))
            if not user or not user.is_active or user.session_version != claims["ver"]:
                raise AppError("UNAUTHENTICATED", "Session revoked", 401)
            CaseService.get_case(db, case_id)
            user_id = user.id
        await websocket.accept()
        audit(user_id, "case.events_read", {"case_id": str(case_id)})
        cursor = max(0, after)
        while True:
            decode_token(websocket.cookies.get(SESSION_COOKIE, ""))
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if not user or not user.is_active or user.session_version != claims["ver"]:
                    await websocket.close(code=4401)
                    return
                rows = db.scalars(
                    select(CaseEvent)
                    .where(CaseEvent.case_id == case_id, CaseEvent.id > cursor)
                    .order_by(CaseEvent.id)
                    .limit(100)
                ).all()
                payloads = [
                    {
                        "id": r.id,
                        "type": r.kind,
                        "payload": r.payload_json,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rows
                ]
            for payload in payloads:
                await websocket.send_json(payload)
                cursor = payload["id"]
            if not payloads:
                await websocket.send_json({"type": "heartbeat", "cursor": cursor})
            await asyncio.sleep(0.4)
    except (AppError, ValueError):
        await websocket.close(code=4401)
    except WebSocketDisconnect:
        return


@router.get("/admin/swap-services")
def swap_services(user: User = Depends(admin), db: Session = Depends(get_db)):
    row = db.get(Setting, "swap_services")
    return row.value if row else {"services": []}


@router.put("/admin/swap-services", dependencies=mutation)
def configure_swap_services(
    data: list[SwapServiceInput], user: User = Depends(admin), db: Session = Depends(get_db)
):
    if len(data) > 20:
        raise AppError("SWAP_SERVICE_LIMIT", "Configure at most 20 swap services", 422)
    row = db.get(Setting, "swap_services")
    if not row:
        row = Setting(key="swap_services", value={})
        db.add(row)
    row.value = {"services": [service.model_dump(mode="json") for service in data]}
    db.commit()
    audit(user.id, "swap_services.configured", {"count": len(data)}, db=db)
    return row.value

import math
import statistics
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.adapters.registry import AdapterRegistry
from app.core.errors import AppError
from app.models.entities import now
from app.models.intelligence import AddressProfileRecord, GraphEdge, GraphNode, Label, TraceRun
from app.models.monitoring import Alert, DormancyState, FenceMember, FenceProposal, MonitorSettings
from app.models.workflow import TaintRun
from app.services.cases import CaseService
from app.services.validation import address_info


def serial(row):
    return {
        c.name: (
            str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v
        )
        for c in row.__table__.columns
        if (v := getattr(row, c.name)) is not None
    }


class MonitorService:
    @staticmethod
    def settings(db, case_id):
        CaseService.get_case(db, case_id)
        row = db.get(MonitorSettings, case_id)
        if not row:
            row = MonitorSettings(case_id=case_id)
            db.add(row)
            db.commit()
        return row

    @staticmethod
    def victims(db, case_id):
        case = CaseService.get_case(db, case_id)
        return {(v.chain, v.victim_address) for v in case.transactions if v.victim_address} | {
            tuple(v) for v in MonitorService.settings(db, case_id).victims_json
        }

    @staticmethod
    def add(db, case_id, chain, address, actor, source="manual"):
        address = address_info(chain, address)[0]
        if (chain, address) in MonitorService.victims(db, case_id):
            raise AppError("VICTIM_FENCE_BLOCKED", "Victim wallets cannot be fence members", 409)
        row = db.scalar(
            select(FenceMember).where(
                FenceMember.case_id == case_id,
                FenceMember.chain == chain,
                FenceMember.address == address,
            )
        )
        if not row:
            row = FenceMember(
                case_id=case_id, chain=chain, address=address, added_by=actor.id, source=source
            )
            db.add(row)
            db.commit()
        return row

    @staticmethod
    def propose(db, case_id, chain, address, direction, evidence):
        if (chain, address) in MonitorService.victims(db, case_id):
            return
        member = db.scalar(
            select(FenceMember.id).where(
                FenceMember.case_id == case_id,
                FenceMember.chain == chain,
                FenceMember.address == address,
            )
        )
        existing = db.scalar(
            select(FenceProposal).where(
                FenceProposal.case_id == case_id,
                FenceProposal.chain == chain,
                FenceProposal.address == address,
                FenceProposal.direction == direction,
            )
        )
        if not member and not existing:
            db.add(
                FenceProposal(
                    case_id=case_id,
                    chain=chain,
                    address=address,
                    direction=direction,
                    reason_json=evidence,
                )
            )

    @staticmethod
    def propose_graph(db, run):
        case = CaseService.get_case(db, run.case_id)
        nodes = db.scalars(select(GraphNode).where(GraphNode.trace_run_id == run.id)).all()
        edges = db.scalars(select(GraphEdge).where(GraphEdge.trace_run_id == run.id)).all()
        members = {
            (m.chain, m.address)
            for m in db.scalars(select(FenceMember).where(FenceMember.case_id == case.id))
        }
        by_id = {n.id: n for n in nodes}
        for v in case.transactions:
            if v.suspect_address:
                MonitorService.propose(
                    db,
                    case.id,
                    v.chain,
                    v.suspect_address,
                    "linked",
                    {"score": 1, "signal": "reported_suspect", "requires_officer_decision": True},
                )
        for n in nodes:
            inbound = [e for e in edges if e.to_node == n.id]
            total = sum(int(e.amount) for e in inbound)
            share = (
                sum(
                    int(e.amount)
                    for e in inbound
                    if (by_id[e.from_node].chain, by_id[e.from_node].address) in members
                )
                / total
                if total
                else 0
            )
            profiles = {
                x.address: db.get(
                    AddressProfileRecord,
                    (x.chain, x.address, str(case.scenario_id) if case.scenario_id else "live"),
                )
                for x in nodes
            }

            def funder_of(x):
                p = profiles.get(x.address)
                return (
                    (p.payload_json.get("gas_funder") or p.payload_json.get("activated_by"))
                    if p
                    else None
                )

            funder = funder_of(n)
            shared = bool(
                funder
                and any(funder_of(x) == funder for x in nodes if (x.chain, x.address) in members)
            )
            targets = {e.to_node for e in edges if e.from_node == n.id}
            swept = any(
                targets & {e.to_node for e in edges if e.from_node == x.id}
                for x in nodes
                if (x.chain, x.address) in members and x.id != n.id
            )
            score = 0.4 * shared + 0.3 * share + 0.3 * swept
            if score > 0.5:
                MonitorService.propose(
                    db,
                    case.id,
                    n.chain,
                    n.address,
                    "linked",
                    {
                        "score": score,
                        "shared_funder": shared,
                        "flow_share_from_fence": share,
                        "sweeps_with_fence": swept,
                    },
                )
        db.commit()

    @staticmethod
    def alert(db, case_id, kind, key, payload):
        row = db.scalar(select(Alert).where(Alert.case_id == case_id, Alert.event_key == key))
        if not row:
            row = Alert(case_id=case_id, kind=kind, event_key=key, payload_json=payload)
            db.add(row)
            db.flush()
            from app.models.workflow import Notification

            case = CaseService.get_case(db, case_id)
            db.add(
                Notification(
                    user_id=case.created_by,
                    kind=kind,
                    payload_json={"case_id": str(case_id), "alert_id": str(row.id), **payload},
                )
            )
        return row

    @staticmethod
    async def poll(db, case_id, actor, as_of=None):
        case = CaseService.get_case(db, case_id)
        settings = MonitorService.settings(db, case_id)
        end = as_of or now()
        if as_of and not case.scenario_id:
            raise AppError("REPLAY_ONLY", "Replay time is available only for synthetic cases", 422)
        registry = AdapterRegistry(db, case.scenario_id)
        members = db.scalars(select(FenceMember).where(FenceMember.case_id == case_id)).all()
        identities = {(m.chain, m.address) for m in members}
        taint = db.scalar(
            select(TaintRun).where(TaintRun.case_id == case_id).order_by(TaintRun.created_at.desc())
        )
        flows = taint.result_json.get("flows", []) if taint else []
        identities |= {
            tuple(f["target"][:2])
            for f in flows
            if int(f.get("by_case", {}).get(str(case_id), "0")) > 0
        }
        identities -= MonitorService.victims(db, case_id)
        errors = []
        wakes = []
        for chain, address in sorted(identities):
            member = next((m for m in members if (m.chain, m.address) == (chain, address)), None)
            state = db.scalar(
                select(DormancyState).where(
                    DormancyState.case_id == case_id,
                    DormancyState.chain == chain,
                    DormancyState.address == address,
                )
            )
            if not state:
                state = DormancyState(
                    case_id=case_id,
                    chain=chain,
                    address=address,
                    threshold_days=settings.threshold_days,
                )
                db.add(state)
                db.flush()
            cursor = state.cursor_json or {}
            since = datetime.fromisoformat(cursor["time"]) if cursor.get("time") else None
            try:
                events = await registry.get(chain).get_transfers(address, "both", since, end)
            except Exception as exc:
                errors.append(
                    {
                        "chain": chain,
                        "address": address,
                        "error": getattr(exc, "code", "PROVIDER_UNAVAILABLE"),
                    }
                )
                continue
            events.sort(key=lambda t: (t.block_time, t.tx_hash, t.log_index))
            seen = set(cursor.get("ids", []))
            boundary_ids = set(seen)
            last_time = since
            history_logs = list(cursor.get("log_amounts", []))
            for t in events:
                key = f"{chain}:{t.tx_hash}:{t.log_index}"
                if since and t.block_time == since and key in seen:
                    continue
                if member:
                    outside = t.to_addr if t.from_addr == address else t.from_addr
                    if (chain, outside) not in identities or (chain, outside) not in {
                        (m.chain, m.address) for m in members
                    }:
                        direction = "out" if t.from_addr == address else "in"
                        MonitorService.propose(
                            db,
                            case_id,
                            chain,
                            outside,
                            direction,
                            {
                                "signal": "fence_crossing",
                                "transfer": t.model_dump(mode="json"),
                                "score": None,
                            },
                        )
                        MonitorService.alert(
                            db,
                            case_id,
                            "FENCE_CROSS",
                            f"cross:{key}:{address}",
                            {
                                "direction": direction,
                                "address": address,
                                "outside_address": outside,
                                "transfer": t.model_dump(mode="json"),
                            },
                        )
                        if direction == "out":
                            label = db.scalar(
                                select(Label).where(
                                    Label.chain == chain,
                                    Label.address == outside,
                                    Label.scenario_key
                                    == (str(case.scenario_id) if case.scenario_id else "live"),
                                )
                            )
                            if label:
                                MonitorService.alert(
                                    db,
                                    case_id,
                                    "VASP_REACHED",
                                    f"vasp:{key}",
                                    {
                                        "entity_id": str(label.entity_id),
                                        "transfer": t.model_dump(mode="json"),
                                        "request_draft": {
                                            "kind": "DISCLOSURE",
                                            "target_chain": chain,
                                            "target_address": outside,
                                            "case_id": str(case_id),
                                            "state": "PRE_DRAFT",
                                            "requires_impact_and_routing_review": True,
                                            "automatically_sent": False,
                                        },
                                    },
                                )
                gap = (
                    (t.block_time - state.last_active).total_seconds() / 86400
                    if state.last_active
                    else 0
                )
                asset = [chain, address, t.token, t.token_address, t.decimals]
                balance = 0
                for f in flows:
                    if datetime.fromisoformat(f["at"]) >= t.block_time:
                        continue
                    value = int(f.get("by_case", {}).get(str(case_id), "0"))
                    if f["target"] == asset:
                        balance += value
                    if f["source"] == asset:
                        balance -= value
                z = 0.0
                if len(history_logs) >= 2 and statistics.pstdev(history_logs) > 0:
                    z = (
                        math.log(max(1, int(t.amount))) - statistics.mean(history_logs)
                    ) / statistics.pstdev(history_logs)
                if (
                    t.from_addr == address
                    and gap >= settings.threshold_days
                    and balance > 0
                    and (z > 2 or int(t.amount) * 10 >= balance)
                ):
                    row = MonitorService.alert(
                        db,
                        case_id,
                        "DORMANT_WAKE",
                        f"wake:{key}:{address}",
                        {
                            "chain": chain,
                            "address": address,
                            "quiet_days": gap,
                            "threshold_days": settings.threshold_days,
                            "z_score": z,
                            "pre_wake_tainted_amount": str(balance),
                            "transfer": t.model_dump(mode="json"),
                            "retrace_status": "PENDING",
                        },
                    )
                    if not row.payload_json.get("retrace_job_id"):
                        wakes.append(row)
                if t.from_addr == address:
                    history_logs = (history_logs + [math.log(max(1, int(t.amount)))])[-100:]
                state.last_active = t.block_time
                state.tainted_balance_json = {
                    "asset": asset,
                    "pre_event_amount": str(max(0, balance)),
                }
                if last_time != t.block_time:
                    boundary_ids = set()
                last_time = t.block_time
                boundary_ids.add(key)
            state.threshold_days = settings.threshold_days
            state.dormant_since = (
                state.last_active
                if state.last_active
                and end - state.last_active >= timedelta(days=settings.threshold_days)
                else None
            )
            state.cursor_json = {
                "time": last_time.isoformat() if last_time else None,
                "ids": sorted(boundary_ids),
                "log_amounts": history_logs,
            }
            if member:
                member.cursor_json = state.cursor_json
        settings.last_polled = now()
        db.commit()
        for alert in wakes + list(
            db.scalars(select(Alert).where(Alert.case_id == case_id, Alert.kind == "DORMANT_WAKE"))
        ):
            if alert.payload_json.get("retrace_job_id"):
                continue
            try:
                from app.models.intelligence import Job
                from app.schemas.intelligence import TraceParams
                from app.services.jobs import dispatch
                from app.services.traces import TraceService

                payload = alert.payload_json
                run = TraceService.start(
                    db,
                    case_id,
                    TraceParams(
                        start_chain=payload["chain"],
                        start_address=payload["address"],
                        trigger_id=alert.id,
                        time_from=datetime.fromisoformat(payload["transfer"]["block_time"]),
                    ),
                    actor,
                )
                job = db.get(Job, run.job_id)
                job.payload_json = {**job.payload_json, "refresh_taint": True}
                alert.payload_json = {
                    **payload,
                    "retrace_job_id": str(job.id),
                    "retrace_status": "QUEUED",
                }
                db.commit()
                dispatch(job.id)
            except AppError as exc:
                alert.payload_json = {
                    **alert.payload_json,
                    "retrace_status": "RETRY",
                    "retrace_error": exc.code,
                }
                db.commit()
        run = db.scalar(
            select(TraceRun)
            .where(TraceRun.case_id == case_id, TraceRun.status == "COMPLETED")
            .order_by(TraceRun.started_at.desc())
        )
        if run:
            MonitorService.propose_graph(db, run)
        return {
            "polled_at": end.isoformat(),
            "watched_addresses": len(identities),
            "errors": errors,
        }

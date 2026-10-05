import hashlib
import heapq
import json
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.adapters.registry import AdapterRegistry
from app.adapters.storage import store_transfers
from app.core.audit import audit
from app.core.errors import AppError
from app.engines.bridges import BridgeDecoder
from app.engines.patterns import detect_patterns
from app.engines.signals import detect_sweeps, mixer_score
from app.models.entities import now
from app.models.intelligence import (
    CaseEvent,
    Entity,
    GraphEdge,
    GraphNode,
    Job,
    PatternFinding,
    TraceRun,
    TransferRecord,
)
from app.services.cases import CaseService
from app.services.labels import LabelService


def emit(db, case_id, kind, payload):
    db.add(CaseEvent(case_id=case_id, kind=kind, payload_json=payload))
    db.commit()


def latest_trace(db, case_id, completed=False):
    query = select(TraceRun).where(TraceRun.case_id == case_id)
    if completed:
        query = query.where(TraceRun.status == "COMPLETED")
    row = db.scalar(query.order_by(TraceRun.started_at.desc()))
    if not row:
        raise AppError("TRACE_NOT_FOUND", "Run a trace first", 404)
    return row


class TraceService:
    @staticmethod
    def start(db, case_id, params, actor):
        case = CaseService.get_case(db, case_id)
        if case.status == "CLOSED":
            raise AppError("CASE_CLOSED", "Closed cases cannot be traced", 409)
        inputs = [
            {
                "id": str(t.id),
                "hash": t.tx_hash,
                "suspect": t.suspect_address if not t.tx_hash else None,
                "chain": t.chain,
            }
            for t in case.transactions
        ]
        key = hashlib.sha256(
            json.dumps(
                [params.model_dump(mode="json"), inputs, str(case.scenario_id)], sort_keys=True
            ).encode()
        ).hexdigest()
        run = db.scalar(
            select(TraceRun).where(TraceRun.case_id == case_id, TraceRun.request_key == key)
        )
        if run:
            if run.status == "FAILED":
                run.status = "QUEUED"
                job = db.get(Job, run.job_id)
                job.status = "QUEUED"
                job.error_json = None
                case.status = "TRACING"
                db.commit()
            return run
        running = db.scalar(
            select(TraceRun).where(
                TraceRun.case_id == case_id, TraceRun.status.in_(["QUEUED", "RUNNING"])
            )
        )
        if running:
            raise AppError("TRACE_IN_PROGRESS", "A trace is already running for this case", 409)
        job = Job(case_id=case_id, kind="trace", payload_json={})
        db.add(job)
        db.flush()
        run = TraceRun(
            case_id=case_id,
            job_id=job.id,
            request_key=key,
            params_json=params.model_dump(mode="json"),
        )
        db.add(run)
        try:
            db.flush()
            job.payload_json = {"trace_run_id": str(run.id)}
            case.status = "TRACING"
            db.commit()
        except IntegrityError:
            db.rollback()
            return db.scalar(
                select(TraceRun).where(TraceRun.case_id == case_id, TraceRun.request_key == key)
            )
        audit(actor.id, "trace.started", {"case_id": str(case_id), "job_id": str(job.id)}, db=db)
        return run

    @staticmethod
    async def run(db, run, params):
        case = CaseService.get_case(db, run.case_id)
        registry = AdapterRegistry(db, case.scenario_id)
        run.status = "RUNNING"
        db.commit()
        for table in [PatternFinding, GraphEdge, GraphNode]:
            db.execute(delete(table).where(table.trace_run_id == run.id))
        db.commit()
        nodes, edges, queue, seen, stops = {}, [], [], {}, Counter()
        counter = 0

        def node(chain, address, hop, role="unknown"):
            key = (chain, address)
            if key not in nodes:
                n = GraphNode(
                    trace_run_id=run.id,
                    chain=chain,
                    address=address,
                    hop=hop,
                    role=role,
                    evidence_json=[],
                )
                db.add(n)
                db.flush()
                nodes[key] = n
                emit(
                    db,
                    case.id,
                    "node_added",
                    {"id": str(n.id), "chain": chain, "address": address, "hop": hop, "role": role},
                )
            else:
                nodes[key].hop = min(nodes[key].hop, hop)
            return nodes[key]

        for victim in case.transactions:
            adapter = registry.get(victim.chain)
            if not victim.suspect_address:
                transfers = await adapter.get_tx(victim.tx_hash)
                matching = [
                    t
                    for t in transfers
                    if (
                        not victim.victim_address
                        or t.from_addr == victim.victim_address
                        or victim.victim_address in t.metadata.get("input_addresses", [])
                    )
                    and (not victim.token or t.token == victim.token)
                    and (not victim.amount or t.amount == victim.amount)
                ]
                if len(matching) != 1:
                    raise AppError(
                        "AMBIGUOUS_TRANSACTION",
                        "Transaction has multiple transfers; specify victim wallet, token and amount",
                        422,
                        {"candidates": [t.model_dump(mode="json") for t in matching]},
                    )
                transfer = matching[0]
                victim.victim_address, victim.suspect_address = transfer.from_addr, transfer.to_addr
                victim.token, victim.amount, victim.decimals = (
                    transfer.token,
                    transfer.amount,
                    transfer.decimals,
                )
                victim.tx_time, victim.status = transfer.block_time, "REPORTED"
                db.commit()
            incoming_time = (
                victim.tx_time or params.time_from or datetime(1970, 1, 1, tzinfo=timezone.utc)
            )
            origin = node(victim.chain, victim.suspect_address, 0)
            if victim.victim_address:
                victim_node = node(victim.chain, victim.victim_address, -1, "victim")
                if victim.tx_hash:
                    candidates = await adapter.get_tx(victim.tx_hash)
                    for t in candidates:
                        if t.to_addr != victim.suspect_address:
                            continue
                        record = store_transfers(db, [t], case.scenario_id)[0]
                        edge = GraphEdge(
                            trace_run_id=run.id,
                            from_node=victim_node.id,
                            to_node=origin.id,
                            transfer_id=record.id,
                            amount=t.amount,
                            token=t.token,
                            decimals=t.decimals,
                            block_time=t.block_time,
                            evidence_json=t.evidence,
                        )
                        if not any(
                            e.transfer_id == record.id and e.to_node == origin.id for e in edges
                        ):
                            db.add(edge)
                            edges.append(edge)
            counter += 1
            heapq.heappush(
                queue,
                (
                    -int(victim.amount or "1"),
                    counter,
                    origin.chain,
                    origin.address,
                    0,
                    incoming_time,
                    victim.token,
                    None,
                ),
            )
        if params.start_address:
            queue.clear()
            origin = node(params.start_chain.value, params.start_address, 0)
            heapq.heappush(
                queue,
                (
                    -1,
                    counter + 1,
                    origin.chain,
                    origin.address,
                    0,
                    params.time_from or now(),
                    None,
                    None,
                ),
            )
        while queue:
            amount, _, chain, address, hop, arrival, token, token_address = heapq.heappop(queue)
            key = (chain, address, token, token_address)
            if key in seen and seen[key] <= arrival:
                continue
            seen[key] = arrival
            current = nodes[(chain, address)]
            reason = None
            labels = LabelService.lookup(db, chain, address, case.scenario_id)
            if labels:
                current.label_id = labels[0].id
                reason = "labeled_entity"
                current.role = (
                    "exchange"
                    if db.get(Entity, labels[0].entity_id).type == "exchange"
                    else db.get(Entity, labels[0].entity_id).type
                )
            elif hop >= params.max_hops:
                reason = "hop_limit"
            elif len(nodes) >= params.max_nodes:
                reason = "node_limit"
            adapter = registry.get(chain)
            if not reason:
                incoming = await adapter.get_transfers(address, "in")
                outgoing = await adapter.get_transfers(address, "out")
                profile_map = {
                    t.to_addr: await adapter.get_profile(t.to_addr) for t in outgoing[:30]
                }
                mixer = mixer_score(incoming, outgoing, profile_map)
                if mixer["likely_mixer"]:
                    current.role = "mixer"
                    current.evidence_json = mixer["evidence"]
                    reason = "mixer_uncertain"
                if not reason:
                    depositors = {t.from_addr for t in incoming}
                    sweepers = []
                    if len(depositors) >= 3:
                        for sender in list(depositors)[:30]:
                            sweeps = detect_sweeps(
                                await adapter.get_transfers(sender, "in"),
                                await adapter.get_transfers(sender, "out"),
                            )
                            if any(s["target"] == address for s in sweeps):
                                sweepers.append(sender)
                    if len(sweepers) >= 3:
                        current.role = "hub_candidate"
                        current.evidence_json = [
                            {"signal": "hub_sweeps", "distinct_deposit_candidates": len(sweepers)}
                        ]
                        reason = "hub_detected"
                    elif len(outgoing) > params.high_volume_degree:
                        reason = "high_volume_noise"
                    else:
                        sweeps = detect_sweeps(incoming, outgoing)
                        if sweeps:
                            current.role = "deposit_candidate"
                            current.evidence_json = sweeps[0]["evidence"]
            if reason:
                current.evidence_json = [
                    *current.evidence_json,
                    {"signal": "stop_rule", "reason": reason},
                ]
                stops[reason] += 1
                db.commit()
                continue
            start = max(arrival, params.time_from) if params.time_from else arrival
            outgoing = [
                t
                for t in outgoing
                if t.block_time >= start
                and (not params.time_to or t.block_time <= params.time_to)
                and int(t.amount) >= int(params.min_amount)
                and (not token or t.token == token)
                and (not token_address or t.token_address == token_address)
            ]
            outgoing.sort(key=lambda t: (-int(t.amount), t.block_time, t.tx_hash, t.log_index))
            if len(outgoing) > params.max_fanout:
                stops["fanout_pruned"] += len(outgoing) - params.max_fanout
            outgoing = outgoing[: params.max_fanout]
            if not outgoing:
                stops["no_outgoing_funds"] += 1
            for t in outgoing:
                if len(nodes) >= params.max_nodes:
                    stops["node_limit"] += 1
                    break
                destination_chain, destination_address, next_token, next_contract, next_amount = (
                    t.chain.value,
                    t.to_addr,
                    t.token,
                    t.token_address,
                    t.amount,
                )
                evidence = t.evidence
                inferred = False
                try:
                    transition = await BridgeDecoder.resolve(t, adapter, registry)
                except AppError as exc:
                    if exc.code != "UNSUPPORTED_BRIDGE":
                        raise
                    transition = None
                    destination = node(chain, t.to_addr, hop + 1, "bridge")
                    destination.evidence_json = [
                        {
                            "signal": "stop_rule",
                            "reason": "unsupported_bridge",
                            "message": exc.message,
                        }
                    ]
                    stops["unsupported_bridge"] += 1
                else:
                    destination = node(chain, t.to_addr, hop + 1)
                record = store_transfers(db, [t], case.scenario_id)[0]
                if not any(
                    e.transfer_id == record.id and e.to_node == destination.id for e in edges
                ):
                    edge = GraphEdge(
                        trace_run_id=run.id,
                        from_node=current.id,
                        to_node=destination.id,
                        transfer_id=record.id,
                        amount=t.amount,
                        token=t.token,
                        decimals=t.decimals,
                        block_time=t.block_time,
                        evidence_json=evidence,
                    )
                    db.add(edge)
                    edges.append(edge)
                    db.flush()
                    emit(
                        db,
                        case.id,
                        "edge_added",
                        {
                            "id": str(edge.id),
                            "source": str(current.id),
                            "target": str(destination.id),
                            "amount": t.amount,
                            "token": t.token,
                        },
                    )
                if destination.evidence_json and any(
                    e.get("reason") == "unsupported_bridge" for e in destination.evidence_json
                ):
                    continue
                if transition:
                    destination.role = (
                        "bridge" if transition["kind"] == "bridge" else "swap_service"
                    )
                    destination.evidence_json = transition["evidence"]
                    destination_chain, destination_address = (
                        transition["destination_chain"],
                        transition["recipient"],
                    )
                    next_token, next_contract, next_amount = (
                        transition["token"],
                        transition.get("token_address"),
                        transition["amount"],
                    )
                    inferred = transition["inferred"]
                    recipient = node(destination_chain, destination_address, hop + 2)
                    edge = GraphEdge(
                        trace_run_id=run.id,
                        from_node=destination.id,
                        to_node=recipient.id,
                        transfer_id=record.id,
                        amount=next_amount,
                        token=next_token,
                        decimals=transition["decimals"],
                        block_time=transition.get("block_time", t.block_time),
                        inferred=inferred,
                        evidence_json=[
                            *transition["evidence"],
                            {"signal": "transition_asset", "asset_contract": next_contract},
                        ],
                    )
                    db.add(edge)
                    edges.append(edge)
                    destination = recipient
                counter += 1
                heapq.heappush(
                    queue,
                    (
                        -min(-amount, int(next_amount)),
                        counter,
                        destination_chain,
                        destination_address,
                        destination.hop,
                        transition.get("block_time", t.block_time) if transition else t.block_time,
                        next_token,
                        next_contract,
                    ),
                )
            job = db.get(Job, run.job_id)
            job.progress = min(95, int(100 * len(seen) / max(1, len(seen) + len(queue))))
            db.commit()
        for finding in detect_patterns(list(nodes.values()), edges):
            db.add(
                PatternFinding(
                    trace_run_id=run.id,
                    kind=finding["kind"],
                    node_ids_json=finding["nodes"],
                    score=finding["score"],
                    evidence_json=finding["evidence"],
                )
            )
        run.status = "COMPLETED"
        run.finished_at = now()
        run.stop_summary_json = dict(stops)
        case.status = "OPEN"
        db.commit()
        from app.services.monitoring import MonitorService

        MonitorService.propose_graph(db, run)
        emit(db, case.id, "trace_done", {"trace_run_id": str(run.id), "stop_summary": dict(stops)})
        return {
            "trace_run_id": str(run.id),
            "nodes": len(nodes),
            "edges": len(edges),
            "stop_summary": dict(stops),
        }


def graph_json(db, run):
    nodes = db.scalars(
        select(GraphNode).where(GraphNode.trace_run_id == run.id).order_by(GraphNode.hop)
    ).all()
    edges = db.scalars(
        select(GraphEdge).where(GraphEdge.trace_run_id == run.id).order_by(GraphEdge.block_time)
    ).all()
    return {
        "trace_run_id": str(run.id),
        "status": run.status,
        "stop_summary": run.stop_summary_json,
        "nodes": [
            {
                "data": {
                    "id": str(n.id),
                    "chain": n.chain,
                    "address": n.address,
                    "label": n.address[:8] + "…" + n.address[-5:],
                    "hop": n.hop,
                    "role": n.role,
                    "cluster_id": str(n.cluster_id) if n.cluster_id else None,
                    "evidence": n.evidence_json,
                }
            }
            for n in nodes
        ],
        "edges": [
            {
                "data": {
                    "id": str(e.id),
                    "source": str(e.from_node),
                    "target": str(e.to_node),
                    "amount": e.amount,
                    "token": e.token,
                    "decimals": e.decimals,
                    "tx_hash": db.get(TransferRecord, e.transfer_id).tx_hash,
                    "log_index": db.get(TransferRecord, e.transfer_id).log_index,
                    "time": e.block_time.isoformat(),
                    "inferred": e.inferred,
                    "evidence": e.evidence_json,
                }
            }
            for e in edges
        ],
    }

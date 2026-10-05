import hashlib
import hmac
import math
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from statistics import median

import networkx as nx
from sqlalchemy import select

from app.adapters.registry import AdapterRegistry
from app.core.audit import AuditService, canonical
from app.core.config import get_settings
from app.core.errors import AppError
from app.models.entities import Case, User, now
from app.models.intelligence import (
    AddressProfileRecord,
    Attribution,
    GraphEdge,
    GraphNode,
    TraceRun,
    TransferRecord,
)
from app.models.workflow import (
    CaseCollision,
    CaseSimilarity,
    GangCase,
    GangCaseMember,
    Notification,
    OperatorFingerprint,
    TaintRun,
    WalletFarm,
)
from app.services.cases import CaseService
from app.services.traces import latest_trace


class GangService:
    @staticmethod
    def detail(db, gang_id):
        gang = db.get(GangCase, gang_id)
        if not gang:
            raise AppError("GANG_NOT_FOUND", "Gang case not found", 404)
        cases = list(db.scalars(select(Case).where(Case.gang_case_id == gang_id)))
        assets = defaultdict(lambda: defaultdict(int))
        summaries = []
        seen_runs = set()
        for case in cases:
            run = db.scalar(
                select(TaintRun)
                .where(TaintRun.case_id == case.id)
                .order_by(TaintRun.created_at.desc())
            )
            if run:
                key = tuple(
                    run.result_json["target"].get(k)
                    for k in ["chain", "address", "token", "token_address", "decimals"]
                )
                summaries.append(
                    {
                        "case_id": str(case.id),
                        "target": run.result_json["target"],
                        "amount": run.result_json["by_case"].get(str(case.id), "0"),
                    }
                )
                for colour, amount in run.result_json["by_case"].items():
                    identity = (key, colour)
                    if identity not in seen_runs:
                        assets[key][colour] += int(amount)
                        seen_runs.add(identity)
        return {
            "id": str(gang.id),
            "name": gang.name,
            "cases": [{"id": str(c.id), "case_ref": c.case_ref, "title": c.title} for c in cases],
            "evidence": gang.evidence_json,
            "targets": [
                {
                    "chain": key[0],
                    "address": key[1],
                    "token": key[2],
                    "token_address": key[3],
                    "decimals": key[4],
                    "by_case": {k: str(v) for k, v in values.items()},
                    "total": str(sum(values.values())),
                }
                for key, values in assets.items()
            ],
            "summaries": summaries,
        }

    @staticmethod
    def split(db, gang_id, available, target=None):
        detail = GangService.detail(db, gang_id)
        targets = detail["targets"]
        if len(targets) != 1:
            raise AppError(
                "AMBIGUOUS_RESTITUTION_ASSET",
                "Restitution requires a single target asset; do not combine chains or currencies",
                422,
            )
        values = {key: int(value) for key, value in targets[0]["by_case"].items()}
        from app.engines.taint import allocate

        usable = min(int(available), sum(values.values()))
        amounts = allocate(usable, values)
        return {
            "available": str(available),
            "allocated": str(usable),
            "unallocated": str(int(available) - usable),
            "by_case": {key: str(value) for key, value in amounts.items()},
            "preview_only": True,
            "evidence": [
                {
                    "signal": "proportional_restitution",
                    "note": "Largest-remainder rounding preserves exact base units; each award is capped at its traceable share.",
                }
            ],
        }

    @staticmethod
    async def farms(db, case_id):
        case = CaseService.get_case(db, case_id)
        trace = latest_trace(db, case_id, completed=True)
        groups = defaultdict(list)
        if case.scenario_id:
            profiles = [
                p.payload_json
                for p in db.scalars(
                    select(AddressProfileRecord).where(
                        AddressProfileRecord.scenario_key == str(case.scenario_id)
                    )
                )
            ]
        else:
            registry = AdapterRegistry(db)
            nodes = list(db.scalars(select(GraphNode).where(GraphNode.trace_run_id == trace.id)))
            profiles = [
                (await registry.get(n.chain).get_profile(n.address)).model_dump(mode="json")
                for n in nodes
            ]
        reference_time = (
            max(
                (datetime.fromisoformat(p["last_seen"]) for p in profiles if p.get("last_seen")),
                default=now(),
            )
            if case.scenario_id
            else now()
        )
        for profile in profiles:
            funder = profile.get("gas_funder") or profile.get("activated_by")
            if funder and profile["tx_count"] <= 3 and profile.get("first_seen"):
                first_seen = datetime.fromisoformat(profile["first_seen"])
                if not 0 <= (reference_time - first_seen).total_seconds() <= 30 * 86400:
                    continue
                groups[(profile["chain"], funder)].append(
                    (datetime.fromisoformat(profile["first_seen"]), profile["address"])
                )
        for (chain, funder), members in groups.items():
            if len(members) < 10:
                continue
            members.sort()
            best = []
            for start, _ in members:
                candidate = [
                    (at, address)
                    for at, address in members
                    if 0 <= (at - start).total_seconds() <= 3600
                ]
                if len(candidate) > len(best):
                    best = candidate
            if len(best) < 10:
                continue
            row = db.scalar(
                select(WalletFarm).where(
                    WalletFarm.case_id == case_id, WalletFarm.funder_address == funder
                )
            )
            if not row:
                row = WalletFarm(
                    case_id=case_id,
                    funder_address=funder,
                    member_count=len(best),
                    first_seen_window=int((best[-1][0] - best[0][0]).total_seconds()),
                    evidence_json={
                        "chain": chain,
                        "members": [address for _, address in best],
                        "signal": "shared_funder_fresh_low_activity",
                        "source": "simulated" if case.scenario_id else "observed",
                        "as_of": reference_time.isoformat(),
                        "max_age_days": 30,
                        "max_tx_count": 3,
                        "probabilistic": True,
                    },
                )
                db.add(row)
        db.commit()
        return [
            {
                "id": str(row.id),
                "funder": row.funder_address,
                "members": row.member_count,
                "window_seconds": row.first_seen_window,
                "status": row.status,
                "evidence": row.evidence_json,
            }
            for row in db.scalars(select(WalletFarm).where(WalletFarm.case_id == case_id))
        ]

    @staticmethod
    def approve_farm(db, farm_id, actor):
        if actor.role != "INVESTIGATOR":
            raise AppError("FORBIDDEN", "Investigator approval required", 403)
        farm = db.get(WalletFarm, farm_id)
        if not farm:
            raise AppError("FARM_NOT_FOUND", "Wallet farm not found", 404)
        case = db.get(Case, farm.case_id)
        if not case.gang_case_id:
            gang = GangCase(name="Officer-approved wallet farm", evidence_json=[])
            db.add(gang)
            db.flush()
            case.gang_case_id = gang.id
            db.add(GangCaseMember(gang_case_id=gang.id, case_id=case.id))
        gang = db.get(GangCase, case.gang_case_id)
        if farm.status != "approved":
            gang.evidence_json = [
                *gang.evidence_json,
                {
                    "signal": "approved_wallet_farm",
                    "farm_id": str(farm.id),
                    "approved_by": str(actor.id),
                    **farm.evidence_json,
                },
            ]
            farm.status = "approved"
            AuditService.append(
                db, actor.id, "farm.approved", {"case_id": str(case.id), "farm_id": str(farm.id)}
            )
            db.commit()
        return {"status": farm.status, "gang_case_id": str(case.gang_case_id)}

    @staticmethod
    def fingerprint(db, case):
        trace = db.scalar(
            select(TraceRun)
            .where(TraceRun.case_id == case.id, TraceRun.status == "COMPLETED")
            .order_by(TraceRun.started_at.desc())
        )
        if not trace:
            return None
        edges = list(db.scalars(select(GraphEdge).where(GraphEdge.trace_run_id == trace.id)))
        vector = defaultdict(float)
        hours = Counter(e.block_time.hour for e in edges)
        for hour, value in hours.items():
            vector[f"hour:{hour}"] = value / max(1, len(edges))
        delays = []
        fees = []
        for e in edges:
            record = db.get(TransferRecord, e.transfer_id)
            fee = record.metadata_json.get("fee", record.metadata_json.get("gas_price", 0))
            fees.append(math.log1p(max(0, float(fee))))
            protocol = record.metadata_json.get("protocol")
            if protocol:
                vector["protocol:" + str(protocol)] += 1 / max(1, len(edges))
            source = db.get(GraphNode, e.from_node)
            vector["chain:" + source.chain] += 1 / max(1, len(edges))
            if source.role in {"bridge", "swap_service", "hot_wallet", "exchange"}:
                vector["role:" + source.role] += 1 / max(1, len(edges))
            for before in edges:
                if before.to_node == e.from_node and before.block_time <= e.block_time:
                    delays.append((e.block_time - before.block_time).total_seconds())
        attribution = db.scalar(select(Attribution).where(Attribution.trace_run_id == trace.id))
        if attribution and attribution.entity_id and attribution.status != "needs_review":
            vector["exit:" + str(attribution.entity_id)] = 1
        vector["median_delay"] = math.log1p(median(delays)) / 10 if delays else 0
        vector["median_fee"] = median(fees) / 30 if fees else 0
        batches = Counter(e.block_time.isoformat() for e in edges)
        for size, count in Counter(batches.values()).items():
            vector["batch:" + str(min(size, 20))] = count / max(1, len(batches))
        vector["batch_size"] = min(1, median(batches.values()) / 10) if batches else 0
        row = db.get(OperatorFingerprint, case.id)
        if not row:
            row = OperatorFingerprint(case_id=case.id, vector_json={})
            db.add(row)
        row.vector_json, row.computed_at = dict(vector), now()
        db.flush()
        return vector

    @staticmethod
    def similar(db, case_id):
        case = CaseService.get_case(db, case_id)
        own = GangService.fingerprint(db, case)
        if not own:
            return []
        results = []
        for other in db.scalars(select(Case).where(Case.id != case_id)):
            vector = GangService.fingerprint(db, other)
            if not vector:
                continue
            norm = math.sqrt(sum(v * v for v in own.values()) * sum(v * v for v in vector.values()))
            score = sum(v * vector.get(k, 0) for k, v in own.items()) / norm if norm else 0
            row = db.get(CaseSimilarity, (case_id, other.id))
            if not row:
                row = CaseSimilarity(case_a=case_id, case_b=other.id, result_json={})
                db.add(row)
            row.result_json = {
                "cosine": score,
                "suggested": score >= 0.9,
                "features": {"current": dict(own), "other": dict(vector)},
                "automatic_link": False,
            }
            results.append(
                {"case_id": str(other.id), "case_ref": other.case_ref, **row.result_json}
            )
        db.commit()
        return sorted(results, key=lambda r: r["cosine"], reverse=True)[:20]

    @staticmethod
    def collisions(db, user):
        cases = list(db.scalars(select(Case)))
        signatures = {}
        for case in cases:
            trace = db.scalar(
                select(TraceRun)
                .where(TraceRun.case_id == case.id, TraceRun.status == "COMPLETED")
                .order_by(TraceRun.started_at.desc())
            )
            signatures[case.id] = (
                {
                    hmac.new(
                        get_settings().jwt_secret.encode(),
                        canonical([case.scenario_id, n.chain, n.address]).encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    for n in db.scalars(select(GraphNode).where(GraphNode.trace_run_id == trace.id))
                    if n.role not in {"victim", "hub_candidate", "hot_wallet", "exchange"}
                }
                if trace
                else set()
            )
        for index, left in enumerate(cases):
            for right in cases[index + 1 :]:
                a, b = db.get(User, left.created_by), db.get(User, right.created_by)
                if a.org_unit == b.org_unit:
                    continue
                overlap = signatures[left.id] & signatures[right.id]
                if not overlap:
                    continue
                identity = sorted([left.id, right.id], key=str)
                hashed = sorted(overlap)[0]
                row = db.scalar(
                    select(CaseCollision).where(
                        CaseCollision.case_a == identity[0],
                        CaseCollision.case_b == identity[1],
                        CaseCollision.hashed_cluster_id == hashed,
                    )
                )
                if not row:
                    row = CaseCollision(
                        case_a=identity[0], case_b=identity[1], hashed_cluster_id=hashed
                    )
                    db.add(row)
                    db.flush()
                    for officer in [a, b]:
                        db.add(
                            Notification(
                                user_id=officer.id,
                                kind="collision",
                                payload_json={
                                    "collision_id": str(row.id),
                                    "note": "An overlapping infrastructure cluster was found in another unit; no complaint details are shared.",
                                },
                            )
                        )
        db.commit()
        result = []
        for row in db.scalars(select(CaseCollision)):
            members = [db.get(Case, cid) for cid in [row.case_a, row.case_b]]
            own = next(
                (c for c in members if db.get(User, c.created_by).org_unit == user.org_unit), None
            )
            if own:
                result.append(
                    {
                        "id": str(row.id),
                        "own_case_id": str(own.id),
                        "status": row.status,
                        "hashed_cluster_id": row.hashed_cluster_id,
                        "room": row.room_json,
                    }
                )
        return result

    @staticmethod
    def open_room(db, collision_id, user):
        visible = {r["id"] for r in GangService.collisions(db, user)}
        if str(collision_id) not in visible:
            raise AppError("FORBIDDEN", "Collision is restricted to the participating units", 403)
        row = db.get(CaseCollision, collision_id)
        if not row.room_json:
            row.room_json = {
                "id": str(uuid.uuid4()),
                "units": [
                    db.get(User, db.get(Case, cid).created_by).org_unit
                    for cid in [row.case_a, row.case_b]
                ],
                "opened_at": now().isoformat(),
                "opened_by": str(user.id),
                "messages": [],
            }
            row.status = "room_open"
            AuditService.append(db, user.id, "collision.room_opened", {"collision_id": str(row.id)})
            db.commit()
        return row.room_json

    @staticmethod
    def communities(db, gang_id):
        detail = GangService.detail(db, gang_id)
        graph = nx.Graph()
        for case in detail["cases"]:
            graph.add_node(case["id"], kind="case")
            trace = db.scalar(
                select(TraceRun)
                .where(TraceRun.case_id == uuid.UUID(case["id"]), TraceRun.status == "COMPLETED")
                .order_by(TraceRun.started_at.desc())
            )
            if trace:
                for n in db.scalars(select(GraphNode).where(GraphNode.trace_run_id == trace.id)):
                    if n.role not in {"victim", "hot_wallet", "hub_candidate", "exchange"}:
                        address = n.chain + ":" + n.address
                        graph.add_node(address, kind="address")
                        graph.add_edge(case["id"], address, weight=1)
        case_ids = [uuid.UUID(case["id"]) for case in detail["cases"]]
        for farm in db.scalars(
            select(WalletFarm).where(
                WalletFarm.case_id.in_(case_ids), WalletFarm.status == "approved"
            )
        ):
            chain = farm.evidence_json["chain"]
            funder = chain + ":" + farm.funder_address
            graph.add_node(funder, kind="address")
            graph.add_edge(str(farm.case_id), funder, weight=farm.member_count)
            for member in farm.evidence_json["members"]:
                address = chain + ":" + member
                graph.add_node(address, kind="address")
                graph.add_edge(funder, address, weight=1)
                graph.add_edge(str(farm.case_id), address, weight=1)
        groups = (
            nx.community.louvain_communities(graph, seed=42, weight="weight")
            if graph.number_of_edges()
            else [{n} for n in graph]
        )
        return {
            "communities": [sorted(g) for g in groups],
            "nodes": [{"id": n, **data} for n, data in graph.nodes(data=True)],
            "edges": [{"source": a, "target": b} for a, b in graph.edges()],
            "evidence": [{"signal": "weighted_case_address_louvain", "candidate_structure": True}],
        }

import asyncio
import hashlib
import json
import secrets
import time
import uuid

import httpx
from sqlalchemy import select

from app.core.audit import AuditService, audit
from app.core.config import get_settings
from app.core.errors import AppError
from app.models.entities import now
from app.models.intelligence import (
    Attribution,
    FederatedQuery,
    FederatedReply,
    GraphNode,
    Job,
    Vasp,
)
from app.services.cases import CaseService
from app.services.labels import LabelService
from app.services.privacy import multiply, point, salted_hash, scalar
from app.services.traces import emit, latest_trace


class FederatedLookupService:
    @staticmethod
    def start(db, case_id, mode, actor):
        case = CaseService.get_case(db, case_id)
        if case.status == "CLOSED":
            raise AppError("CASE_CLOSED", "Closed cases cannot start lookups", 409)
        run = latest_trace(db, case_id, completed=True)
        running = db.scalar(
            select(FederatedQuery)
            .join(Job, FederatedQuery.job_id == Job.id)
            .where(FederatedQuery.case_id == case_id, Job.status.in_(["QUEUED", "RUNNING"]))
        )
        if running:
            return running
        nodes = db.scalars(
            select(GraphNode)
            .where(GraphNode.trace_run_id == run.id, GraphNode.role != "victim")
            .order_by(GraphNode.hop.desc())
        ).all()
        selected = [
            n
            for n in nodes
            if n.role in {"hub_candidate", "hot_wallet", "deposit_candidate", "exchange"}
        ]
        selected = (
            selected or [n for n in nodes if n.role not in {"mixer", "bridge", "swap_service"}][:10]
        )
        if not selected:
            raise AppError("NO_LOOKUP_TARGETS", "No suitable trace frontier for lookup", 422)
        targets = [
            {"chain": n.chain, "address": n.address, "node_id": str(n.id), "hop": n.hop}
            for n in selected[:100]
        ]
        salt = secrets.token_hex(32)
        job = Job(case_id=case_id, kind="federated", payload_json={})
        db.add(job)
        db.flush()
        query = FederatedQuery(
            case_id=case_id,
            trace_run_id=run.id,
            job_id=job.id,
            query_hash=hashlib.sha256(json.dumps(targets, sort_keys=True).encode()).hexdigest(),
            salt=salt,
            mode=mode,
            targets_json=targets,
        )
        db.add(query)
        db.flush()
        job.payload_json = {"query_id": str(query.id)}
        vasps = db.scalars(select(Vasp)).all()
        if not vasps:
            db.rollback()
            raise AppError("VASP_DIRECTORY_EMPTY", "Configure a VASP directory first", 503)
        for vasp in vasps:
            db.add(FederatedReply(query_id=query.id, vasp_id=vasp.id, raw_json={}))
        db.commit()
        audit(
            actor.id if actor else None,
            "federated.started",
            {"case_id": str(case_id), "query_id": str(query.id), "mode": mode},
            db=db,
        )
        return query

    @staticmethod
    async def request(vasp, query, client):
        targets = query.targets_json
        headers = {"Authorization": "Bearer " + get_settings().sahyog_service_token}
        timeout = get_settings().federated_timeout_seconds
        if query.mode == "hash":
            hashes = [salted_hash(query.salt, t["chain"], t["address"]) for t in targets]
            response = await client.post(
                vasp.endpoint + "/lookup",
                json={"salt": query.salt, "hashes": hashes, "query_id": str(query.id)},
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            matches = payload.get("matched_hashes", [])
            if (
                not isinstance(matches, list)
                or not set(matches).issubset(set(hashes))
                or payload.get("match") != bool(matches)
            ):
                raise ValueError("Invalid lookup reply")
            indices = [i for i, value in enumerate(hashes) if value in matches]
        else:
            secret = scalar()
            blinded = [multiply(secret, point(t["chain"], t["address"])) for t in targets]
            response = await client.post(
                vasp.endpoint + "/psi/round1",
                json={"points": blinded, "query_id": str(query.id)},
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            evaluated, owned = payload["evaluated"], payload["owned"]
            if len(evaluated) != len(targets) or len(owned) > 10000:
                raise ValueError("Invalid PSI response size")
            transformed = {multiply(secret, value): value for value in owned}
            for value in evaluated:
                multiply(secret, value)
            indices = [i for i, value in enumerate(evaluated) if value in transformed]
            own_matches = [transformed[evaluated[i]] for i in indices]
            response = await client.post(
                vasp.endpoint + "/psi/round2",
                json={"session_id": payload["session_id"], "matched_points": own_matches},
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            if response.json().get("match") != bool(indices):
                raise ValueError("PSI confirmation mismatch")
            payload = {"match": bool(indices), "privacy": "ecdh_psi", "source": vasp.source}
        return indices, {
            "match": bool(indices),
            "mode": query.mode,
            "source": vasp.source,
            "protocol": "salted_hmac" if query.mode == "hash" else "ecdh_psi",
        }

    @staticmethod
    async def run(db, query, transport=None):
        vasps = db.scalars(select(Vasp).order_by(Vasp.name)).all()
        case = CaseService.get_case(db, query.case_id)
        async with httpx.AsyncClient(transport=transport) as client:

            async def collect(vasp):
                reply = db.scalar(
                    select(FederatedReply).where(
                        FederatedReply.query_id == query.id, FederatedReply.vasp_id == vasp.id
                    )
                )
                if reply.status != "pending":
                    return
                started = time.monotonic()
                indices, details, status = [], {}, "timeout"
                for attempt in range(2):
                    try:
                        indices, details = await FederatedLookupService.request(vasp, query, client)
                        status = "yes" if indices else "no"
                        break
                    except httpx.TimeoutException:
                        status = "timeout"
                    except (httpx.HTTPError, ValueError, KeyError, TypeError):
                        status = "error"
                    if attempt == 0:
                        await asyncio.sleep(0.2)
                elapsed = time.monotonic() - started
                reply.match = bool(indices) if status in {"yes", "no"} else None
                reply.status, reply.received_at, reply.response_s = status, now(), elapsed
                reply.raw_json = details if details else {"status": status, "source": vasp.source}
                responded = status in {"yes", "no"}
                vasp.attempts += 1
                vasp.successes += int(responded)
                vasp.response_rate = vasp.successes / vasp.attempts
                if responded:
                    vasp.avg_response_s += (elapsed - vasp.avg_response_s) / vasp.successes
                measurement = 1 / (1 + elapsed / 2) if responded else 0
                vasp.responsiveness_ewma = 0.3 * measurement + 0.7 * vasp.responsiveness_ewma
                vasp.last_updated = now()
                db.commit()
                for index in indices:
                    target = query.targets_json[index]
                    try:
                        label = LabelService.verified(
                            db,
                            target["chain"],
                            target["address"],
                            vasp.entity_id,
                            query.id,
                            vasp.id,
                            vasp.source,
                            case.scenario_id,
                        )
                    except AppError as exc:
                        reply.status = "conflict"
                        reply.raw_json = {
                            "code": exc.code,
                            "source": vasp.source,
                            "manual_review_required": True,
                        }
                        result = db.scalar(
                            select(Attribution).where(
                                Attribution.trace_run_id == query.trace_run_id
                            )
                        )
                        if result:
                            result.status = "needs_review"
                            result.confidence = 0
                            result.evidence_json = [
                                *result.evidence_json,
                                {"signal": "conflicting_vasp_claims", "query_id": str(query.id)},
                            ]
                        case.status = "OPEN"
                        db.commit()
                        continue
                    node = db.get(GraphNode, uuid.UUID(target["node_id"]))
                    node.label_id = label.id
                    node.role = (
                        "hot_wallet"
                        if node.role in {"hub_candidate", "hot_wallet"}
                        else "deposit_candidate"
                    )
                    result = db.scalar(
                        select(Attribution).where(Attribution.trace_run_id == query.trace_run_id)
                    )
                    if not result:
                        result = Attribution(
                            case_id=query.case_id,
                            trace_run_id=query.trace_run_id,
                            confidence=0,
                            evidence_json=[],
                            candidates_json=[],
                            source="simulated" if case.scenario_id else "observed",
                        )
                        db.add(result)
                    if result.status == "confirmed" and result.entity_id != vasp.entity_id:
                        reply.status = "conflict"
                        result.status = "needs_review"
                        result.confidence = 0
                        result.evidence_json = [
                            *result.evidence_json,
                            {"signal": "conflicting_vasp_claims", "query_id": str(query.id)},
                        ]
                        case.status = "OPEN"
                    elif result.status != "needs_review":
                        result.entity_id = vasp.entity_id
                        result.status, result.confidence, result.hops = (
                            "confirmed",
                            0.95,
                            target["hop"],
                        )
                        result.evidence_json = [
                            e
                            for e in result.evidence_json
                            if e.get("signal") != "federated_confirmation"
                        ] + [
                            {
                                "signal": "federated_confirmation",
                                "weight": 0.95,
                                "verified": True,
                                "query_id": str(query.id),
                                "vasp_id": str(vasp.id),
                                "node_id": target["node_id"],
                                "source": vasp.source,
                                "privacy_mode": query.mode,
                            }
                        ]
                        case.status = "ATTRIBUTED"
                        AuditService.append(
                            db,
                            None,
                            "case.attributed",
                            {
                                "case_id": str(case.id),
                                "status": "confirmed",
                                "entity_id": str(vasp.entity_id),
                                "confidence": 0.95,
                                "query_id": str(query.id),
                            },
                            entity="case",
                            entity_id=case.id,
                        )
                    db.commit()
                done = len(
                    db.scalars(
                        select(FederatedReply).where(
                            FederatedReply.query_id == query.id, FederatedReply.status != "pending"
                        )
                    ).all()
                )
                db.get(Job, query.job_id).progress = int(done / len(vasps) * 100)
                db.commit()
                emit(
                    db,
                    query.case_id,
                    "federated_reply",
                    {"query_id": str(query.id), "vasp": vasp.name, "status": reply.status},
                )

            await asyncio.gather(*(collect(vasp) for vasp in vasps))
        emit(db, query.case_id, "federated_done", {"query_id": str(query.id)})
        return {
            "query_id": str(query.id),
            "confirmed": not any(
                r.status == "conflict"
                for r in db.scalars(
                    select(FederatedReply).where(FederatedReply.query_id == query.id)
                )
            )
            and any(
                r.status == "yes"
                for r in db.scalars(
                    select(FederatedReply).where(FederatedReply.query_id == query.id)
                )
            ),
        }

    @staticmethod
    def latest(db, case_id):
        query = db.scalar(
            select(FederatedQuery)
            .where(FederatedQuery.case_id == case_id)
            .order_by(FederatedQuery.sent_at.desc())
        )
        if not query:
            raise AppError("LOOKUP_NOT_FOUND", "Start a federated lookup first", 404)
        replies = db.scalars(
            select(FederatedReply).where(FederatedReply.query_id == query.id)
        ).all()
        job = db.get(Job, query.job_id)
        return {
            "id": str(query.id),
            "job_id": str(job.id),
            "status": job.status,
            "progress": job.progress,
            "mode": query.mode,
            "sent_at": query.sent_at,
            "source": "simulated"
            if all(db.get(Vasp, r.vasp_id).source == "simulated" for r in replies)
            else "live",
            "replies": [
                {
                    "vasp_id": str(r.vasp_id),
                    "vasp": db.get(Vasp, r.vasp_id).name,
                    "status": r.status,
                    "match": r.match,
                    "received_at": r.received_at,
                    "response_s": r.response_s,
                    "evidence": r.raw_json,
                }
                for r in replies
            ],
        }

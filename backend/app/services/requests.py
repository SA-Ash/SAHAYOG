import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.adapters.registry import AdapterRegistry
from app.core.audit import AuditService
from app.core.errors import AppError
from app.models.entities import Case, User, now
from app.models.intelligence import Job, RoutingDecision
from app.models.workflow import (
    BlastRadiusReport,
    FreezeRequest,
    Notification,
    RequestEvent,
    TaintRun,
)
from app.services.impact import RULES, ImpactService
from app.services.sahyog import get_sahyog_client
from app.services.traces import emit, latest_trace

TRANSITIONS = {
    "DRAFT": {"PROPOSED", "SENT"},
    "RETURNED": {"PROPOSED", "SENT"},
    "PROPOSED": {"APPROVED", "REJECTED", "RETURNED", "SENT"},
    "APPROVED": {"SENT", "EXPIRED"},
    "SENT": {"ACKNOWLEDGED", "FROZEN", "DECLINED", "EXPIRED", "RELEASED"},
    "ACKNOWLEDGED": {"FROZEN", "DECLINED", "EXPIRED", "RELEASED"},
    "FROZEN": {"EXPIRED", "RELEASED"},
    "DECLINED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
    "RELEASED": set(),
}


class RequestService:
    @staticmethod
    def get(db, request_id, locked=False):
        stmt = select(FreezeRequest).where(FreezeRequest.id == request_id)
        row = db.scalar(
            stmt.with_for_update().execution_options(populate_existing=True) if locked else stmt
        )
        if not row:
            raise AppError("REQUEST_NOT_FOUND", "Request not found", 404)
        return row

    @staticmethod
    def transition(db, row, state, actor, note):
        if state not in TRANSITIONS.get(row.state, set()):
            raise AppError("ILLEGAL_TRANSITION", f"Cannot move {row.state} to {state}", 409)
        previous = row.state
        row.state, row.updated_at, row.version = state, now(), row.version + 1
        db.add(
            RequestEvent(
                request_id=row.id,
                from_state=previous,
                to_state=state,
                actor_id=actor.id if actor else None,
                note=note,
            )
        )
        AuditService.append(
            db,
            actor.id if actor else None,
            "request." + state.lower(),
            {
                "case_id": str(row.case_id),
                "request_id": str(row.id),
                "from": previous,
                "to": state,
                "note": note,
            },
            entity="request",
            entity_id=row.id,
        )
        users = (
            list(
                db.scalars(select(User).where(User.is_active.is_(True), User.role == "SUPERVISOR"))
            )
            if state == "PROPOSED"
            else [db.get(User, row.created_by)]
        )
        for user in users:
            db.add(
                Notification(
                    user_id=user.id,
                    request_id=row.id,
                    kind="request_" + state.lower(),
                    payload_json={"case_id": str(row.case_id), "state": state},
                )
            )

    @staticmethod
    def draft(db, case_id, data, actor):
        impact = ImpactService.latest(db, case_id)
        taint = db.get(TaintRun, impact.taint_run_id)
        if taint.trace_run_id != latest_trace(db, case_id, completed=True).id:
            raise AppError("STALE_IMPACT", "Recompute impact for the current trace", 409)
        if data.kind != "DISCLOSURE" and not impact.result_json["freeze_allowed"]:
            raise AppError(
                "FREEZE_BLOCKED",
                "Freeze is blocked by target impact or swept funds; request review/disclosure",
                409,
            )
        amount = int(data.amount or impact.traceable_amount) if data.kind != "DISCLOSURE" else 0
        if data.kind != "DISCLOSURE" and (amount <= 0 or amount > int(impact.traceable_amount)):
            raise AppError("INVALID_FREEZE_AMOUNT", "Amount exceeds traceable available funds", 422)
        from app.engines.taint import allocate

        allocations = allocate(amount, impact.result_json["amount_by_case"])
        routing = db.scalar(
            select(RoutingDecision)
            .where(RoutingDecision.case_id == case_id)
            .order_by(RoutingDecision.created_at.desc())
        )
        row = FreezeRequest(
            case_id=case_id,
            kind=data.kind,
            created_by=actor.id,
            target_vasp_id=routing.target_vasp_id if routing else None,
            target_chain=impact.target_chain,
            target_address=impact.target_address,
            amount_by_case_json={key: str(value) for key, value in allocations.items()},
            issuer_target=routing.issuer_target if routing else None,
            impact_level=impact.impact_level,
            package_json={
                "impact_id": str(impact.id),
                "impact": impact.result_json,
                "taint_run_id": str(taint.id),
                "trace_run_id": str(taint.trace_run_id),
                "expiry_minutes": data.expiry_minutes,
                "mock_outcome": data.mock_outcome,
                "source": "simulated" if db.get(Case, case_id).scenario_id else "observed",
                "post_review_required": impact.impact_level == "LOW",
            },
        )
        db.add(row)
        db.flush()
        db.add(
            RequestEvent(
                request_id=row.id,
                from_state="",
                to_state="DRAFT",
                actor_id=actor.id,
                note="Evidence package prepared",
            )
        )
        AuditService.append(
            db,
            actor.id,
            "request.drafted",
            {"case_id": str(case_id), "request_id": str(row.id)},
            entity="request",
            entity_id=row.id,
        )
        db.commit()
        return row

    @staticmethod
    def validate_package(db, row):
        if row.package_json.get("forecast_only"):
            raise AppError(
                "FORECAST_ONLY",
                "Forecast drafts require observed impact and target confirmation before proposal",
                409,
            )
        impact = db.get(BlastRadiusReport, uuid.UUID(row.package_json["impact_id"]))
        if (
            not impact
            or impact.id != ImpactService.latest(db, row.case_id).id
            or uuid.UUID(row.package_json["trace_run_id"])
            != latest_trace(db, row.case_id, completed=True).id
        ):
            raise AppError(
                "STALE_PACKAGE", "The evidence package changed; create a fresh draft", 409
            )
        if row.kind != "DISCLOSURE":
            if (now() - impact.created_at).total_seconds() > RULES["max_evidence_age_minutes"] * 60:
                raise AppError(
                    "STALE_BALANCE", "Refresh the balance and impact before freeze approval", 409
                )
            if not impact.result_json["freeze_allowed"]:
                raise AppError(
                    "FREEZE_BLOCKED", "Hub, swept or high-impact target cannot be frozen", 409
                )
            if sum(int(v) for v in row.amount_by_case_json.values()) > int(impact.traceable_amount):
                raise AppError(
                    "INVALID_FREEZE_AMOUNT", "Freeze exceeds current impact allowance", 409
                )

    @staticmethod
    def check_reservations(db, row):
        if row.kind == "DISCLOSURE":
            return
        available = row.package_json["impact"]["amount_by_case"]
        target = row.package_json["impact"]["target"]
        reserved = {}
        for other in db.scalars(
            select(FreezeRequest).where(
                FreezeRequest.id != row.id,
                FreezeRequest.target_chain == row.target_chain,
                FreezeRequest.target_address == row.target_address,
                FreezeRequest.state.in_(["APPROVED", "SENT", "ACKNOWLEDGED", "FROZEN"]),
            )
        ):
            asset = other.package_json.get("impact", {}).get("target", {})
            if any(
                asset.get(key) != target.get(key) for key in ["token", "token_address", "decimals"]
            ):
                continue
            for case_id, amount in other.amount_by_case_json.items():
                reserved[case_id] = reserved.get(case_id, 0) + int(amount)
        for case_id, amount in row.amount_by_case_json.items():
            if int(amount) + reserved.get(case_id, 0) > int(available.get(case_id, 0)):
                raise AppError(
                    "FUNDS_ALREADY_RESERVED",
                    "Another active request reserves these victim funds",
                    409,
                )

    @staticmethod
    async def preflight(db, row):
        if row.kind == "DISCLOSURE":
            return
        RequestService.validate_package(db, row)
        case = db.get(Case, row.case_id)
        target = row.package_json["impact"]["target"]
        adapter = AdapterRegistry(db, case.scenario_id).get(row.target_chain)
        run = db.get(TaintRun, uuid.UUID(row.package_json["taint_run_id"]))
        known = {
            (flow["at"], flow["amount"])
            for flow in run.result_json["flows"]
            if flow["source"][:2] == [row.target_chain, row.target_address]
        }
        arrival = datetime.fromisoformat(run.result_json["as_of_time"])
        outgoing = await adapter.get_transfers(row.target_address, "out")
        for transfer in outgoing:
            if (
                transfer.token == target["token"]
                and transfer.token_address == target["token_address"]
                and transfer.decimals == target["decimals"]
                and transfer.block_time >= arrival
                and (transfer.block_time.isoformat(), transfer.amount) not in known
            ):
                raise AppError(
                    "STALE_BALANCE",
                    "New outgoing funds require a fresh trace, taint and impact review",
                    409,
                )
        if not case.scenario_id:
            balance = await adapter.get_balance(
                row.target_address,
                target["token_address"] or ("BTC" if row.target_chain == "bitcoin" else None),
            )
            if (
                balance.get("token") != target["token"]
                or balance.get("decimals") != target["decimals"]
            ):
                raise AppError(
                    "BALANCE_ASSET_MISMATCH", "Cannot verify the requested asset balance", 409
                )
            if int(balance["amount"]) < sum(
                int(value) for value in row.amount_by_case_json.values()
            ):
                raise AppError(
                    "INSUFFICIENT_BALANCE",
                    "The observed balance no longer covers this request",
                    409,
                )

    @staticmethod
    def schedule_expiry(row):
        from app.core.config import get_settings

        if get_settings().job_backend == "celery" and row.expires_at:
            from app.workers.tasks import expiry_cycle

            try:
                expiry_cycle.apply_async(eta=row.expires_at)
            except Exception:
                pass

    @staticmethod
    def act(db, request_id, action, actor, reason, elevated=False, minutes=30):
        row = RequestService.get(db, request_id, locked=True)
        supervisor_actions = {"approve", "reject", "return", "confirm", "release"}
        required = "SUPERVISOR" if action in supervisor_actions else "INVESTIGATOR"
        if actor.role != required:
            raise AppError("FORBIDDEN", required + " role required", 403)
        if action in {"approve", "confirm", "release"} and not elevated:
            raise AppError(
                "TWO_FACTOR_REQUIRED", "Current session-bound TOTP elevation required", 403
            )
        if action in supervisor_actions and row.proposer_id == actor.id:
            raise AppError("SAME_USER_TWO_KEYS", "The proposer cannot supply the second key", 403)
        if action in {"propose", "golden-hour"} and actor.id != row.created_by:
            raise AppError("NOT_REQUEST_OWNER", "Only the drafting investigator may propose", 403)
        if action in {"propose", "approve", "golden-hour", "confirm"}:
            AuditService.append(
                db,
                actor.id,
                "request.review_started",
                {"request_id": str(row.id), "action": action},
                entity="request",
                entity_id=row.id,
            )
            if action in {"approve", "golden-hour", "confirm"}:
                RequestService.check_reservations(db, row)
                if row.kind != "DISCLOSURE" and not row.target_vasp_id:
                    raise AppError(
                        "TARGET_VASP_REQUIRED",
                        "Confirm attribution and recommend the VASP route before a hold",
                        409,
                    )
            RequestService.validate_package(db, row)
            if len(reason.strip()) < (
                40 if row.impact_level in {"MEDIUM", "HIGH", "BLOCKED"} else 10
            ):
                raise AppError(
                    "JUSTIFICATION_REQUIRED",
                    "Write a detailed justification for medium/high-impact review",
                    422,
                )
        if action == "propose":
            row.proposer_id, row.reason = actor.id, reason
            RequestService.transition(db, row, "PROPOSED", actor, reason)
            db.get(Case, row.case_id).status = "FREEZE_PENDING"
        elif action == "approve":
            if row.state != "PROPOSED":
                raise AppError("ILLEGAL_TRANSITION", "Approve only a proposed request", 409)
            row.approver_id = actor.id
            row.expires_at = now() + timedelta(minutes=row.package_json["expiry_minutes"])
            RequestService.transition(db, row, "APPROVED", actor, reason)
        elif action in {"reject", "return"}:
            if row.state != "PROPOSED":
                raise AppError("ILLEGAL_TRANSITION", "Review only proposed requests", 409)
            RequestService.transition(
                db, row, "REJECTED" if action == "reject" else "RETURNED", actor, reason
            )
        elif action == "golden-hour":
            if row.state not in {"DRAFT", "RETURNED", "PROPOSED"} or row.kind == "DISCLOSURE":
                raise AppError(
                    "ILLEGAL_TRANSITION", "Golden-hour requires an eligible freeze draft", 409
                )
            row.proposer_id, row.reason, row.golden_hour = actor.id, reason, True
            row.second_key_due = now() + timedelta(minutes=minutes)
            row.expires_at = row.second_key_due
            RequestService.transition(db, row, "SENT", actor, "Temporary hold: " + reason)
        elif action == "confirm":
            if (
                not row.golden_hour
                or row.approver_id
                or row.state not in {"SENT", "ACKNOWLEDGED", "FROZEN"}
            ):
                raise AppError("ILLEGAL_TRANSITION", "No temporary hold awaits a second key", 409)
            if now() >= row.second_key_due:
                raise AppError("GOLDEN_HOUR_LAPSED", "The second-key deadline has passed", 409)
            row.approver_id = actor.id
            row.expires_at = now() + timedelta(minutes=row.package_json["expiry_minutes"])
            db.add(
                RequestEvent(
                    request_id=row.id,
                    from_state=row.state,
                    to_state=row.state,
                    actor_id=actor.id,
                    note="Second key confirmed: " + reason,
                )
            )
            AuditService.append(
                db,
                actor.id,
                "request.second_key_confirmed",
                {"request_id": str(row.id), "reason": reason},
                entity="request",
                entity_id=row.id,
            )
        elif action == "release":
            RequestService.transition(db, row, "RELEASED", actor, reason)
        else:
            raise AppError("UNKNOWN_ACTION", "Unknown request action", 422)
        db.commit()
        if action in {"approve", "golden-hour", "confirm"}:
            RequestService.schedule_expiry(row)
        emit(db, row.case_id, "request_changed", {"request_id": str(row.id), "state": row.state})
        return row

    @staticmethod
    def queue(db, row, operation="send"):
        existing = next(
            (
                j
                for j in db.scalars(
                    select(Job).where(Job.case_id == row.case_id, Job.kind == "request")
                )
                if j.payload_json.get("request_id") == str(row.id)
                and j.payload_json.get("operation") == operation
                and j.status in {"QUEUED", "RUNNING"}
            ),
            None,
        )
        if existing:
            return existing
        job = Job(
            case_id=row.case_id,
            kind="request",
            payload_json={"request_id": str(row.id), "operation": operation},
        )
        db.add(job)
        db.commit()
        from app.services.jobs import dispatch

        dispatch(job.id)
        return job

    @staticmethod
    async def dispatch(db, request_id, operation="send", client=None):
        row = RequestService.get(db, request_id)
        client = client or get_sahyog_client()
        if operation == "release" and row.state not in {"EXPIRED", "RELEASED"}:
            raise AppError(
                "RELEASE_NOT_AUTHORIZED",
                "Release requires a lapsed or supervisor-released request",
                409,
            )
        if operation == "extend" and (
            not row.golden_hour
            or not row.approver_id
            or row.state not in {"SENT", "ACKNOWLEDGED", "FROZEN"}
        ):
            raise AppError(
                "EXTENSION_NOT_AUTHORIZED",
                "Second-key confirmation is required to extend a hold",
                409,
            )
        if operation == "send":
            if row.state not in {"APPROVED", "SENT"} or (
                row.state == "SENT" and row.sahyog_request_id
            ):
                return {"request_id": str(row.id), "state": row.state}
            if row.expires_at and now() >= row.expires_at:
                RequestService.transition(
                    db, row, "EXPIRED", None, "Request lapsed before dispatch"
                )
                db.commit()
                return {"state": row.state}
            if not row.golden_hour and not row.approver_id:
                raise AppError("SECOND_KEY_MISSING", "Approval required before dispatch", 409)
            await RequestService.preflight(db, row)
            RequestService.check_reservations(db, row)
        payload = {
            "case_ref": db.get(Case, row.case_id).case_ref,
            "kind": row.kind,
            "target_address": row.target_address,
            "amount_by_case": row.amount_by_case_json,
            "outcome": row.package_json.get("mock_outcome", "delayed"),
            "request_key": str(row.id) + ":" + operation,
            "action": "RELEASE"
            if operation == "release"
            else "EXTEND"
            if operation == "extend"
            else "HOLD"
            if row.golden_hour
            else "REQUEST",
            "original_request_id": row.sahyog_request_id,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "target_vasp_id": str(row.target_vasp_id) if row.target_vasp_id else None,
        }
        try:
            response = await client.submit_request(payload)
            row = RequestService.get(db, request_id, locked=True)
            if operation in {"release", "extend"}:
                if row.issuer_request_id:
                    await client.submit_request(
                        {
                            **payload,
                            "request_key": str(row.id) + ":issuer:" + operation,
                            "original_request_id": row.issuer_request_id,
                            "target_vasp_id": None,
                            "issuer_target": row.issuer_target,
                        }
                    )
                row.dispatch_error = None
                AuditService.append(
                    db,
                    None,
                    "request." + operation + "_delivered",
                    {"request_id": str(row.id), "external_id": response["id"]},
                    entity="request",
                    entity_id=row.id,
                )
                db.commit()
                return {"state": row.state, operation + "_delivered": True}
            row.sahyog_request_id, row.dispatch_error = response["id"], None
            if row.state in {"EXPIRED", "RELEASED"} or (row.expires_at and now() >= row.expires_at):
                if row.state not in {"EXPIRED", "RELEASED"}:
                    RequestService.transition(
                        db, row, "EXPIRED", None, "Deadline elapsed during dispatch"
                    )
                db.commit()
                return await RequestService.dispatch(db, row.id, "release", client)
            if row.issuer_target and not row.issuer_request_id:
                issuer_response = await client.submit_request(
                    {
                        **payload,
                        "request_key": str(row.id) + ":issuer",
                        "target_vasp_id": None,
                        "issuer_target": row.issuer_target,
                    }
                )
                row.issuer_request_id = issuer_response["id"]
            if row.state == "APPROVED":
                RequestService.transition(db, row, "SENT", None, "Mock SAHYOG accepted dispatch")
            if row.state == "SENT":
                RequestService.transition(
                    db, row, "ACKNOWLEDGED", None, "VASP acknowledged through mock portal"
                )
            if response.get("state") in {"FROZEN", "DECLINED"} and row.state == "ACKNOWLEDGED":
                if row.kind != "DISCLOSURE" or response["state"] == "DECLINED":
                    RequestService.transition(
                        db, row, response["state"], None, "Scripted mock outcome"
                    )
            db.commit()
            emit(
                db, row.case_id, "request_changed", {"request_id": str(row.id), "state": row.state}
            )
            return {"request_id": str(row.id), "state": row.state}
        except AppError as exc:
            db.rollback()
            row = RequestService.get(db, request_id)
            if (
                operation == "extend"
                and exc.code == "HOLD_NOT_ACTIVE"
                and row.state in {"SENT", "ACKNOWLEDGED", "FROZEN"}
            ):
                RequestService.transition(
                    db, row, "EXPIRED", None, "Portal hold lapsed before extension delivery"
                )
                db.commit()
                return await RequestService.dispatch(db, row.id, "release", client)
            row.dispatch_error = {
                "code": exc.code,
                "message": exc.message,
                "retryable": True,
                "operation": operation,
            }
            AuditService.append(
                db,
                None,
                "request.dispatch_failed",
                {"request_id": str(row.id), "code": exc.code},
                entity="request",
                entity_id=row.id,
            )
            db.commit()
            raise

    @staticmethod
    async def poll(db, row, client=None):
        if not row.sahyog_request_id or row.state not in {"SENT", "ACKNOWLEDGED"}:
            return row
        await RequestService.expire(db, client)
        if row.state not in {"SENT", "ACKNOWLEDGED"}:
            return row
        response = await (client or get_sahyog_client()).get_request(row.sahyog_request_id)
        row = RequestService.get(db, row.id, locked=True)
        if row.state not in {"SENT", "ACKNOWLEDGED"}:
            return row
        if row.state == "SENT":
            RequestService.transition(db, row, "ACKNOWLEDGED", None, "Portal status acknowledged")
        state = response.get("state")
        if state in {"EXPIRED", "RELEASED"}:
            RequestService.transition(db, row, state, None, "Portal reports hold no longer active")
            db.commit()
            await RequestService.dispatch(db, row.id, "release", client)
            return row
        if state in {"FROZEN", "DECLINED"} and (row.kind != "DISCLOSURE" or state == "DECLINED"):
            RequestService.transition(db, row, state, None, "Portal status update")
        db.commit()
        return row

    @staticmethod
    async def expire(db, client=None):
        changed = []
        for row in db.scalars(
            select(FreezeRequest).where(
                FreezeRequest.state.in_(["APPROVED", "SENT", "ACKNOWLEDGED", "FROZEN"])
            )
        ):
            deadline = (
                row.second_key_due if row.golden_hour and not row.approver_id else row.expires_at
            )
            if deadline and now() >= deadline:
                RequestService.transition(
                    db,
                    row,
                    "EXPIRED",
                    None,
                    "Second-key deadline lapsed"
                    if row.golden_hour and not row.approver_id
                    else "Request expired",
                )
                db.commit()
                if row.sahyog_request_id:
                    try:
                        await RequestService.dispatch(db, row.id, "release", client)
                    except AppError:
                        pass
                changed.append(str(row.id))
            elif deadline and 0 < (deadline - now()).total_seconds() <= 300:
                for uid in [row.created_by, row.approver_id]:
                    if uid and not db.scalar(
                        select(Notification.id).where(
                            Notification.request_id == row.id,
                            Notification.user_id == uid,
                            Notification.kind == "expiry_reminder",
                        )
                    ):
                        db.add(
                            Notification(
                                user_id=uid,
                                request_id=row.id,
                                kind="expiry_reminder",
                                payload_json={"due": deadline.isoformat()},
                            )
                        )
                db.commit()
        return changed


def request_json(db, row):
    return {
        "id": str(row.id),
        "case_id": str(row.case_id),
        "kind": row.kind,
        "state": row.state,
        "target_chain": row.target_chain,
        "target_address": row.target_address,
        "target_vasp_id": str(row.target_vasp_id) if row.target_vasp_id else None,
        "amount_by_case": row.amount_by_case_json,
        "issuer_target": row.issuer_target,
        "impact_level": row.impact_level,
        "proposer_id": str(row.proposer_id) if row.proposer_id else None,
        "approver_id": str(row.approver_id) if row.approver_id else None,
        "reason": row.reason,
        "golden_hour": row.golden_hour,
        "second_key_due": row.second_key_due,
        "expires_at": row.expires_at,
        "sahyog_request_id": row.sahyog_request_id,
        "issuer_request_id": row.issuer_request_id,
        "package": row.package_json,
        "dispatch_error": row.dispatch_error,
        "timeline": [
            {
                "from": e.from_state,
                "to": e.to_state,
                "actor_id": str(e.actor_id) if e.actor_id else None,
                "note": e.note,
                "at": e.at,
            }
            for e in db.scalars(
                select(RequestEvent)
                .where(RequestEvent.request_id == row.id)
                .order_by(RequestEvent.at)
            )
        ],
    }

import json
import math
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.adapters.registry import AdapterRegistry
from app.core.audit import audit
from app.core.errors import AppError
from app.models.entities import now
from app.models.intelligence import GraphNode
from app.models.workflow import BlastRadiusReport
from app.services.cases import CaseService
from app.services.taint import TaintService
from app.services.traces import latest_trace

RULES = json.loads((Path(__file__).parents[1] / "config" / "impact.json").read_text())


def classify_impact(share, depositors, swept, address_type, hub=False):
    if hub or swept:
        return "BLOCKED", "REVIEW_ONLY", "hub_target" if hub else "funds_swept"
    if (
        address_type in {"pooled", "merchant"}
        or depositors > RULES["high_depositors"]
        or share < Decimal(RULES["high_share"])
    ):
        return "HIGH", "REVIEW_ONLY", "pooled_or_low_traceable_share"
    if share >= Decimal(RULES["low_share"]) and depositors <= RULES["low_depositors"]:
        return "LOW", "EXACT_AMOUNT_FREEZE", "high_share_few_depositors"
    return "MEDIUM", "ACCOUNT_HOLD_PENDING_REVIEW", "intermediate_share_or_depositor_count"


def address_type(incoming):
    counts = Counter(t.from_addr for t in incoming)
    total = sum(counts.values())
    entropy = -sum((n / total) * math.log2(n / total) for n in counts.values()) if total else 0
    times = sorted(t.block_time for t in incoming)
    intervals = [(right - left).total_seconds() for left, right in zip(times, times[1:])]
    mean_interval = sum(intervals) / len(intervals) if intervals else 0
    interval_cv = (
        math.sqrt(sum((value - mean_interval) ** 2 for value in intervals) / len(intervals))
        / mean_interval
        if mean_interval > 0
        else None
    )
    kind = (
        "pooled"
        if len(counts) > RULES["pooled_senders"]
        else "merchant"
        if len(counts) >= RULES["merchant_senders"] and entropy > 2
        else "personal"
    )
    return (
        kind,
        0.7 if counts else 0.2,
        {
            "signal": "address_type_heuristic",
            "unique_senders": len(counts),
            "sender_entropy": entropy,
            "deposit_count": total,
            "average_deposit_base_units": str(
                sum(int(t.amount) for t in incoming) // max(1, total)
            ),
            "mean_inflow_interval_seconds": mean_interval,
            "inflow_interval_coefficient_of_variation": interval_cv,
            "probabilistic": True,
        },
    )


class ImpactService:
    @staticmethod
    def latest(db, case_id):
        row = db.scalar(
            select(BlastRadiusReport)
            .where(BlastRadiusReport.case_id == case_id)
            .order_by(BlastRadiusReport.created_at.desc())
        )
        if not row:
            raise AppError("IMPACT_NOT_FOUND", "Compute Blast Radius first", 404)
        return row

    @staticmethod
    async def compute(db, case_id, data, actor):
        case = CaseService.get_case(db, case_id)
        trace = latest_trace(db, case_id, completed=True)
        taint = TaintService.latest(db, case_id)
        if taint.trace_run_id != trace.id:
            raise AppError("STALE_TAINT", "Recompute Dye Pack for the current trace", 409)
        target = taint.result_json["target"]
        chain, address = (
            (data.chain.value, data.address)
            if data.address
            else (target["chain"], target["address"])
        )
        node = db.scalar(
            select(GraphNode).where(
                GraphNode.trace_run_id == trace.id,
                GraphNode.chain == chain,
                GraphNode.address == address,
            )
        )
        if not node:
            raise AppError("TARGET_NOT_IN_TRACE", "Choose an observed trace address", 422)
        adapter = AdapterRegistry(db, case.scenario_id).get(chain)
        incoming = await adapter.get_transfers(address, "in")
        filtered_in = [
            t
            for t in incoming
            if t.token == target["token"]
            and t.token_address == target["token_address"]
            and t.decimals == target["decimals"]
        ]
        current = next(
            (
                b
                for b in taint.result_json["balances"]
                if b["chain"] == chain
                and b["address"] == address
                and b["token"] == target["token"]
                and b["token_address"] == target["token_address"]
                and b["decimals"] == target["decimals"]
            ),
            None,
        )
        by_case = current["by_case"] if current else {}
        balance = int(current["total_balance"]) if current else 0
        balance_evidence = {
            "signal": "observed_history_estimate",
            "note": "Only the VASP knows the true account balance; on-chain balances may include unrelated customers.",
        }
        if not case.scenario_id:
            observed = await adapter.get_balance(
                address, target["token_address"] or ("BTC" if chain == "bitcoin" else None)
            )
            if (
                observed.get("token") != target["token"]
                or observed.get("decimals") != target["decimals"]
            ):
                raise AppError(
                    "BALANCE_ASSET_MISMATCH", "Cannot estimate this token balance reliably", 422
                )
            balance = int(observed["amount"])
            balance_evidence = {"signal": "provider_balance_estimate", **observed}
        traceable = min(balance, sum(int(v) for v in by_case.values()))
        arrival = datetime.fromisoformat(taint.result_json["as_of_time"])
        coloured_out = [
            flow
            for flow in taint.result_json["flows"]
            if flow["source"][:2] == [chain, address]
            and datetime.fromisoformat(flow["at"]) >= arrival
            and any(int(v) for v in flow["by_case"].values())
        ]
        swept = bool(coloured_out) and traceable < int(taint.result_json["traceable_amount"])
        hub = node.role in {"hub_candidate", "hot_wallet", "exchange"}
        senders = {t.from_addr for t in filtered_in}
        victims = {tx.victim_address for tx in case.transactions}
        others = len(senders - victims)
        kind, confidence, type_evidence = address_type(filtered_in)
        if node.role in {"mixer", "bridge", "swap_service"}:
            kind = "pooled"
        share = Decimal(traceable) / Decimal(balance) if balance else Decimal(0)
        level, recommendation, reason = classify_impact(share, len(senders), swept, kind, hub)
        result = {
            "source": "simulated" if case.scenario_id else "observed",
            "target": {**target, "chain": chain, "address": address},
            "traceable_amount": str(traceable),
            "est_balance": str(balance),
            "amount_by_case": by_case,
            "share_pct": str(share * 100),
            "other_depositors": others,
            "distinct_depositors": len(senders),
            "swept": swept,
            "is_hub": hub,
            "minutes_since_arrival": max(0, (now() - arrival).total_seconds() / 60),
            "address_type": kind,
            "address_type_confidence": confidence,
            "impact_level": level,
            "recommendation": recommendation,
            "freeze_allowed": not hub and not swept and traceable > 0 and level != "HIGH",
            "max_freeze_amount": str(traceable),
            "evidence": [
                {"signal": "impact_rule", "rule": reason, "configuration": RULES},
                type_evidence,
                balance_evidence,
            ],
            "disclaimer": "Estimated on-chain impact only. The VASP must verify the true account balance and depositor ownership.",
        }
        row = BlastRadiusReport(
            case_id=case_id,
            taint_run_id=taint.id,
            target_chain=chain,
            target_address=address,
            traceable_amount=str(traceable),
            est_balance=str(balance),
            impact_level=level,
            swept=swept,
            result_json=result,
        )
        db.add(row)
        db.commit()
        audit(
            actor.id,
            "impact.computed",
            {"case_id": str(case_id), "report_id": str(row.id), "impact": level},
            db=db,
        )
        return {"id": str(row.id), **result}

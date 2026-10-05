from sqlalchemy import select

from app.core.audit import audit
from app.models.intelligence import (
    Attribution,
    FederatedQuery,
    FederatedReply,
    GraphEdge,
    GraphNode,
    RoutingDecision,
    Vasp,
)
from app.services.cases import CaseService
from app.services.traces import latest_trace


def vasp_json(row):
    return {
        "id": str(row.id),
        "entity_id": str(row.entity_id),
        "name": row.name,
        "country": row.country,
        "registered_fiu": row.registered_fiu,
        "channel": row.channel,
        "request_format": row.request_format,
        "avg_response_s": row.avg_response_s,
        "response_rate": row.response_rate,
        "responsiveness_ewma": row.responsiveness_ewma,
        "attempts": row.attempts,
        "last_updated": row.last_updated,
        "source": row.source,
    }


class RoutingService:
    @staticmethod
    def recommend(db, case_id, actor):
        case = CaseService.get_case(db, case_id)
        run = latest_trace(db, case_id, completed=True)
        attribution = db.scalar(select(Attribution).where(Attribution.trace_run_id == run.id))
        directory = sorted(
            db.scalars(select(Vasp)).all(), key=lambda v: v.responsiveness_ewma, reverse=True
        )
        target = next(
            (v for v in directory if attribution and v.entity_id == attribution.entity_id), None
        )
        confirmed = attribution and attribution.status == "confirmed"
        query = db.scalar(
            select(FederatedQuery)
            .where(FederatedQuery.case_id == case_id)
            .order_by(FederatedQuery.sent_at.desc())
        )
        reply = (
            db.scalar(
                select(FederatedReply).where(
                    FederatedReply.query_id == query.id, FederatedReply.vasp_id == target.id
                )
            )
            if target and query
            else None
        )
        reasons = []
        if target and confirmed and target.country == "IN" and target.registered_fiu:
            channel = "DIRECT_PORTAL"
            reasons.append(
                {
                    "rule": "confirmed_registered_indian_vasp",
                    "vasp": target.name,
                    "country": target.country,
                }
            )
        elif (
            target
            and (target.country != "IN")
            and (
                (reply is not None and reply.status in {"timeout", "error"})
                or (target.attempts > 0 and target.response_rate < 0.5)
            )
        ):
            channel = "FIU_LEGAL_ESCALATION"
            reasons.append(
                {
                    "rule": "foreign_nonresponsive_vasp",
                    "vasp": target.name,
                    "response_rate": target.response_rate,
                }
            )
        elif target and confirmed:
            channel = "DIRECT_VASP_REQUEST"
            reasons.append({"rule": "confirmed_responsive_vasp", "vasp": target.name})
        else:
            channel = "MANUAL_REVIEW"
            reasons.append(
                {
                    "rule": "attribution_requires_confirmation",
                    "confidence": attribution.confidence if attribution else 0,
                }
            )
        tokens = set(
            db.scalars(
                select(GraphEdge.token)
                .join(GraphNode, GraphEdge.to_node == GraphNode.id)
                .where(
                    GraphEdge.trace_run_id == run.id,
                    GraphNode.role.in_(["hot_wallet", "exchange"]),
                )
            )
        )
        if not tokens:
            tokens = {tx.token for tx in case.transactions}
        issuer = "TETHER" if "USDT" in tokens else "CIRCLE" if "USDC" in tokens else None
        if issuer:
            reasons.append(
                {
                    "rule": "stablecoin_issuer_path",
                    "issuer": issuer,
                    "action": "Prepare an issuer request for review",
                }
            )
        ranking = [
            {
                "vasp_id": str(v.id),
                "vasp": v.name,
                "responsiveness": v.responsiveness_ewma,
                "country": v.country,
                "source": v.source,
            }
            for v in directory
        ]
        row = RoutingDecision(
            case_id=case_id,
            target_vasp_id=target.id if target else None,
            channel=channel,
            issuer_target=issuer,
            reason_json=reasons,
            ranking_json=ranking,
        )
        db.add(row)
        db.commit()
        audit(actor.id, "routing.recommended", {"case_id": str(case_id), "channel": channel}, db=db)
        return {
            "id": str(row.id),
            "channel": channel,
            "target_vasp": target.name if target else None,
            "issuer_target": issuer,
            "evidence": reasons,
            "ranking": ranking,
            "source": "simulated" if case.scenario_id else "observed",
            "sent": False,
        }

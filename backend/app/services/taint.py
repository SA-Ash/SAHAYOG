import uuid
from collections import defaultdict

from sqlalchemy import select

from app.adapters.registry import AdapterRegistry
from app.adapters.storage import transfer_of
from app.core.audit import audit
from app.core.errors import AppError
from app.engines.taint import Ledger
from app.models.entities import Case, now
from app.models.intelligence import GraphEdge, GraphNode, TraceRun, TransferRecord
from app.models.workflow import GangCase, GangCaseMember, TaintBalance, TaintLot, TaintRun
from app.services.cases import CaseService
from app.services.traces import latest_trace


def account(transfer, address):
    return (
        transfer.chain.value,
        address,
        transfer.token,
        transfer.token_address,
        transfer.decimals,
    )


class TaintService:
    @staticmethod
    def latest(db, case_id):
        row = db.scalar(
            select(TaintRun).where(TaintRun.case_id == case_id).order_by(TaintRun.created_at.desc())
        )
        if not row:
            raise AppError("TAINT_NOT_FOUND", "Run Dye Pack first", 404)
        return row

    @staticmethod
    async def run(db, case_id, data, actor):
        case = CaseService.get_case(db, case_id)
        trace = latest_trace(db, case_id, completed=True)
        nodes = list(db.scalars(select(GraphNode).where(GraphNode.trace_run_id == trace.id)))
        registry = AdapterRegistry(db, case.scenario_id)
        transfers = {}
        for node in nodes:
            for t in await registry.get(node.chain).get_transfers(node.address, "both"):
                transfers[(t.chain.value, t.tx_hash, t.log_index)] = t
        if case.scenario_id:
            for row in db.scalars(
                select(TransferRecord).where(TransferRecord.scenario_id == case.scenario_id)
            ):
                t = transfer_of(row)
                transfers[(t.chain.value, t.tx_hash, t.log_index)] = t
        related = (
            list(db.scalars(select(Case).where(Case.scenario_id == case.scenario_id)))
            if case.scenario_id
            else TaintService.related(db, case, nodes)
        )
        origins = {}
        for member in related:
            for tx in member.transactions:
                if tx.status != "REPORTED" or not tx.amount or not tx.tx_hash:
                    continue
                if (tx.chain, tx.tx_hash) not in {
                    (t.chain.value, t.tx_hash) for t in transfers.values()
                }:
                    for transfer in await registry.get(tx.chain).get_tx(tx.tx_hash):
                        transfers[(transfer.chain.value, transfer.tx_hash, transfer.log_index)] = (
                            transfer
                        )
                candidates = [
                    t
                    for t in transfers.values()
                    if t.chain == tx.chain
                    and t.tx_hash == tx.tx_hash
                    and t.to_addr == tx.suspect_address
                    and (not tx.victim_address or t.from_addr == tx.victim_address)
                    and (not tx.token or t.token == tx.token)
                ]
                if len(candidates) != 1:
                    raise AppError(
                        "AMBIGUOUS_TAINT_ORIGIN",
                        "Resolve a unique victim transfer before tainting",
                        422,
                    )
                t = candidates[0]
                identity = (t.chain.value, t.tx_hash, t.log_index)
                if identity in origins and origins[identity][0] != str(member.id):
                    raise AppError(
                        "DUPLICATE_REPORTED_TRANSFER",
                        "Two cases claim the same loss; resolve before restitution",
                        409,
                    )
                origins[identity] = (str(member.id), tx.amount)
                if not db.scalar(select(TaintLot).where(TaintLot.origin_tx == tx.id)):
                    db.add(TaintLot(case_id=member.id, origin_tx=tx.id, origin_amount=tx.amount))
        if not any(colour == str(case_id) for colour, _ in origins.values()):
            raise AppError(
                "TAINT_ORIGIN_MISSING", "No resolved victim transaction is in this trace", 422
            )
        transitions = defaultdict(list)
        node_by_id = {n.id: n for n in nodes}
        for edge in db.scalars(select(GraphEdge).where(GraphEdge.trace_run_id == trace.id)):
            source, destination = node_by_id[edge.from_node], node_by_id[edge.to_node]
            if source.role in {"bridge", "swap_service"}:
                transitions[edge.transfer_id].append((edge, source, destination))
        protocol = {}
        for record_id, outputs in transitions.items():
            record = db.get(TransferRecord, record_id)
            protocol[(record.chain, record.tx_hash, record.log_index)] = outputs
        mixers = {(n.chain, n.address) for n in nodes if n.role == "mixer"}
        ledgers = {
            method: Ledger(method, data.cutoff) for method in {"haircut", "fifo", data.method}
        }
        pending = []
        ordered = sorted(
            transfers.values(), key=lambda t: (t.block_time, t.chain.value, t.tx_hash, t.log_index)
        )
        for t in ordered:
            identity = (t.chain.value, t.tx_hash, t.log_index)
            pending.append((t.block_time, 0, t, identity, None))
            for edge, source, destination in protocol.get(identity, []):
                pending.append((edge.block_time, 1, t, identity, (edge, source, destination)))
        pending.sort(key=lambda row: (row[0], row[1], row[2].tx_hash, row[2].log_index))
        for at, _, t, identity, transition in pending:
            for ledger in ledgers.values():
                if transition:
                    edge, source, destination = transition
                    sender = account(t, source.address)
                    receiver = (
                        destination.chain,
                        destination.address,
                        edge.token,
                        next(
                            (
                                s.get("asset_contract")
                                for s in edge.evidence_json
                                if s.get("asset_contract")
                            ),
                            None,
                        ),
                        edge.decimals,
                    )
                    ledger.move(
                        sender, receiver, t.amount, at, str(edge.id), output_amount=edge.amount
                    )
                else:
                    ledger.move(
                        account(t, t.from_addr),
                        account(t, t.to_addr),
                        t.amount,
                        at,
                        ":".join(map(str, identity)),
                        origins.get(identity),
                        (t.chain.value, t.from_addr) in mixers,
                    )
        target = TaintService.target(
            nodes, list(db.scalars(select(GraphEdge).where(GraphEdge.trace_run_id == trace.id)))
        )
        summaries = {}
        for method, ledger in ledgers.items():
            coloured = [
                s
                for s in ledger.snapshots
                if s["account"][:2] == list(target) and str(case_id) in s["balance"]
            ]
            candidates = coloured or [
                s for s in ledger.snapshots if s["account"][:2] == list(target)
            ]
            selected = max(
                candidates,
                key=lambda s: sum(int(v) for k, v in s["balance"].items() if k != "uncoloured"),
                default=None,
            )
            summaries[method] = selected
        chosen = summaries[data.method]
        if not chosen:
            chosen = {
                "account": [
                    *target,
                    case.transactions[0].token,
                    None,
                    case.transactions[0].decimals,
                ],
                "at": now().isoformat(),
                "balance": {},
                "uncertain": {},
            }
        asset = chosen["account"]
        for method, summary in list(summaries.items()):
            if summary and summary["account"][2:] != asset[2:]:
                raise AppError(
                    "TAINT_ASSET_AMBIGUOUS",
                    "Method ranges cannot combine different token contracts or precisions",
                    422,
                )
        by_case = {k: v for k, v in chosen["balance"].items() if k != "uncoloured"}
        colours = set(by_case) | {
            colour
            for summary in summaries.values()
            if summary
            for colour in summary["balance"]
            if colour != "uncoloured"
        }
        by_case = {colour: by_case.get(colour, "0") for colour in sorted(colours)}
        ranges = {
            colour: {
                "min": str(
                    min(
                        int((summaries[m] or {}).get("balance", {}).get(colour, 0))
                        for m in ["haircut", "fifo"]
                    )
                ),
                "max": str(
                    max(
                        int((summaries[m] or {}).get("balance", {}).get(colour, 0))
                        for m in ["haircut", "fifo"]
                    )
                ),
            }
            for colour in by_case
        }
        comparison_available = all(
            (
                transfers[identity].chain.value,
                transfers[identity].token,
                transfers[identity].token_address,
                transfers[identity].decimals,
            )
            == (asset[0], asset[2], asset[3], asset[4])
            for identity, (colour, _) in origins.items()
            if colour in by_case
        )
        loss = (
            sum(int(amount) for identity, (colour, amount) in origins.items() if colour in by_case)
            if comparison_available
            else 0
        )
        target_uncertain = any(
            n.chain == target[0] and n.address == target[1] and n.role == "mixer" for n in nodes
        )
        if target_uncertain:
            chosen["uncertain"] = dict(by_case)
            chosen["balance"]["uncoloured"] = str(sum(int(v) for v in chosen["balance"].values()))
            by_case = {}
            ranges = {}
        total = sum(int(v) for v in by_case.values())
        result = {
            "source": "simulated" if case.scenario_id else "observed",
            "method": data.method,
            "cutoff": str(data.cutoff),
            "target": {
                "chain": asset[0],
                "address": asset[1],
                "token": asset[2],
                "token_address": asset[3],
                "decimals": asset[4],
            },
            "by_case": by_case,
            "poison_upper_bounds": {
                case_id: str(sum(int(value) for value in chosen["balance"].values()))
                for case_id in by_case
            }
            if data.method == "poison"
            else {},
            "ranges": ranges,
            "traceable_amount": str(total),
            "uncoloured_amount": chosen["balance"].get("uncoloured", "0"),
            "untraceable_amount": str(sum(int(v) for v in chosen["uncertain"].values())),
            "as_of_time": chosen["at"],
            "balances": ledgers[data.method].result(),
            "flows": ledgers[data.method].edges,
            "related_case_ids": [str(member.id) for member in related],
            "possible_unreported_victims": {
                "amount": str(max(0, total - loss)) if comparison_available else "0",
                "verified": False,
                "comparison_available": comparison_available,
                "note": "Different asset identities require independently evidenced valuation before comparing with reported losses."
                if not comparison_available
                else "Possible excess only; no ownership or criminality is inferred.",
            },
            "evidence": [
                {
                    "signal": "time_ordered_value_ledger",
                    "opening_funds": ledgers[data.method].unknown_funding,
                    "note": "Account-chain taint is a methodological convention. Target amounts are at arrival, before sweeping. Uncoloured funds are not evidence of crime. Uncertain amounts overlap the uncoloured balance and must not be added to it.",
                },
                {
                    "signal": "poison_upper_bound",
                    "note": "Poison bounds may overlap per case; never sum them or use them for restitution.",
                },
            ]
            if data.method == "poison"
            else [
                {
                    "signal": "time_ordered_value_ledger",
                    "opening_funds": ledgers[data.method].unknown_funding,
                    "note": "Account-chain taint is a methodological convention. Target amounts are at arrival, before sweeping. Uncoloured funds are not evidence of crime. Uncertain amounts overlap the uncoloured balance and must not be added to it.",
                }
            ],
        }
        row = TaintRun(
            case_id=case_id,
            trace_run_id=trace.id,
            method=data.method,
            cutoff=str(data.cutoff),
            result_json=result,
        )
        db.add(row)
        db.flush()
        for balance in result["balances"]:
            for colour, value in {
                **balance["by_case"],
                "uncoloured": balance["uncoloured"],
            }.items():
                db.add(
                    TaintBalance(
                        run_id=row.id,
                        chain=balance["chain"],
                        address=balance["address"],
                        token=balance["token"],
                        token_address=balance["token_address"],
                        decimals=balance["decimals"],
                        case_id=uuid.UUID(colour) if colour != "uncoloured" else None,
                        amount=value,
                        method=data.method,
                        as_of_time=pending[-1][0],
                    )
                )
        TaintService.link(db, related, nodes)
        db.commit()
        audit(actor.id, "taint.completed", {"case_id": str(case_id), "run_id": str(row.id)}, db=db)
        return {"id": str(row.id), **result}

    @staticmethod
    def related(db, case, nodes):
        addresses = {
            (n.chain, n.address)
            for n in nodes
            if n.role not in {"victim", "hub_candidate", "hot_wallet", "exchange", "mixer"}
        }
        case_ids = {case.id}
        for node, other in db.execute(
            select(GraphNode, Case)
            .join(TraceRun, GraphNode.trace_run_id == TraceRun.id)
            .join(Case, Case.id == TraceRun.case_id)
            .where(Case.scenario_id.is_(None))
        ):
            if (node.chain, node.address) in addresses:
                case_ids.add(other.id)
        return list(db.scalars(select(Case).where(Case.id.in_(case_ids))))

    @staticmethod
    def target(nodes, edges):
        lookup = {n.id: n for n in nodes}
        candidates = [
            lookup[e.from_node]
            for e in edges
            if lookup[e.to_node].role in {"hub_candidate", "hot_wallet", "exchange"}
            and lookup[e.from_node].role
            not in {"victim", "hub_candidate", "hot_wallet", "exchange"}
        ]
        if not candidates:
            candidates = [n for n in nodes if n.role in {"deposit_candidate", "mixer"}]
        if not candidates:
            candidates = [n for n in nodes if n.role != "victim"]
        if not candidates:
            raise AppError("TAINT_TARGET_MISSING", "Trace has no actionable address", 422)
        target = max(candidates, key=lambda n: n.hop)
        return target.chain, target.address

    @staticmethod
    def link(db, cases, nodes):
        if len(cases) < 2:
            return None
        existing = sorted({c.gang_case_id for c in cases if c.gang_case_id}, key=str)
        gang = (
            db.get(GangCase, existing[0])
            if existing
            else GangCase(
                name="Converging complaint paths",
                evidence_json=[
                    {
                        "signal": "shared_non_hub_path",
                        "addresses": [
                            {"chain": n.chain, "address": n.address}
                            for n in nodes
                            if n.role not in {"victim", "hot_wallet", "hub_candidate", "exchange"}
                        ],
                    }
                ],
            )
        )
        db.add(gang)
        db.flush()
        if existing:
            cases = list(
                {
                    c.id: c
                    for c in [
                        *cases,
                        *db.scalars(select(Case).where(Case.gang_case_id.in_(existing))),
                    ]
                }.values()
            )
        for case in cases:
            case.gang_case_id = gang.id
            if not db.get(GangCaseMember, (gang.id, case.id)):
                db.add(GangCaseMember(gang_case_id=gang.id, case_id=case.id))
        return gang

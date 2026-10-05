import uuid
from collections import defaultdict

from sqlalchemy import delete, select

from app.adapters.registry import AdapterRegistry
from app.core.audit import audit
from app.core.errors import AppError
from app.engines.signals import (
    UnionFind,
    btc_change,
    calibrate,
    decayed_weight,
    detect_sweeps,
    fit_isotonic,
    hub_metrics,
    mixer_score,
    noisy_or,
)
from app.models.entities import Case, now
from app.models.intelligence import (
    Attribution,
    CalibrationMap,
    Cluster,
    ClusterMember,
    Entity,
    FederatedQuery,
    FederatedReply,
    GraphNode,
    Label,
    ScenarioTruth,
)
from app.services.cases import CaseService
from app.services.labels import LabelService
from app.services.probes import ProbeService
from app.services.traces import latest_trace


class AttributionService:
    @staticmethod
    async def run(db, case_id, actor):
        case = CaseService.get_case(db, case_id)
        run = latest_trace(db, case_id, completed=True)
        conflict = db.scalar(
            select(FederatedReply.id)
            .join(FederatedQuery, FederatedReply.query_id == FederatedQuery.id)
            .where(FederatedQuery.trace_run_id == run.id, FederatedReply.status == "conflict")
        )
        if conflict:
            raise AppError(
                "CONFLICTING_VASP_CONFIRMATIONS",
                "Conflicting ownership claims require manual review",
                409,
            )
        registry = AdapterRegistry(db, case.scenario_id)
        nodes = db.scalars(select(GraphNode).where(GraphNode.trace_run_id == run.id)).all()
        lookup = {(n.chain, n.address): n for n in nodes}
        uf, connections = UnionFind(), defaultdict(list)
        histories, profiles, sweeps = {}, {}, {}
        victims = {(tx.chain, tx.victim_address) for tx in case.transactions if tx.victim_address}
        for node in nodes:
            if node.role == "victim":
                continue
            adapter = registry.get(node.chain)
            key = (node.chain, node.address)
            uf.find(key)
            incoming, outgoing = (
                await adapter.get_transfers(node.address, "in"),
                await adapter.get_transfers(node.address, "out"),
            )
            histories[key] = (incoming, outgoing)
            profiles[key] = await adapter.get_profile(node.address)
            sweeps[key] = detect_sweeps(incoming, outgoing)
            if sweeps[key]:
                node.role = "deposit_candidate"
            for sweep in sweeps[key]:
                target = (node.chain, sweep["target"])
                if target in victims:
                    continue
                uf.union(key, target)
                evidence = {
                    "signal": "common_sweep_target",
                    "target": sweep["target"],
                    "count": sweep["count"],
                }
                connections[key].append(evidence)
                connections[target].append(evidence)
            scores = mixer_score(
                incoming,
                outgoing,
                {t.to_addr: await adapter.get_profile(t.to_addr) for t in outgoing[:30]},
            )
            if scores["likely_mixer"]:
                node.role = "mixer"
                node.evidence_json = [*node.evidence_json, *scores["evidence"]]
            if node.chain == "bitcoin":
                change = btc_change(
                    outgoing, {t.to_addr: await adapter.get_profile(t.to_addr) for t in outgoing}
                )
                for t in outgoing:
                    owners = t.metadata.get("input_addresses", [])
                    if len(owners) > 1 and not t.metadata.get("coinjoin") and len(owners) <= 10:
                        for owner in owners:
                            target = (node.chain, owner)
                            if target not in victims:
                                uf.union(key, target)
                                connections[target].append(
                                    {
                                        "signal": "common_input_ownership",
                                        "tx_hash": t.tx_hash,
                                        "probabilistic": True,
                                    }
                                )
                    if t.to_addr in change and (node.chain, t.to_addr) not in victims:
                        target = (node.chain, t.to_addr)
                        uf.union(key, target)
                        connections[target].append(
                            {
                                "signal": "btc_change_heuristic",
                                "tx_hash": t.tx_hash,
                                "probabilistic": True,
                            }
                        )
        funder_groups = defaultdict(list)
        for key, profile in profiles.items():
            funder = profile.gas_funder or profile.activated_by
            if funder:
                funder_groups[(key[0], funder)].append(key)
        for (_, funder), keys in funder_groups.items():
            if len(keys) < 2:
                continue
            for key in keys:
                uf.union(keys[0], key)
                connections[key].append(
                    {"signal": "shared_funder", "address": funder, "probabilistic": True}
                )
        contract_groups = defaultdict(list)
        for key, (_, outgoing) in histories.items():
            targets = {sweep["target"] for sweep in sweeps[key]}
            if not targets or key[0] not in {"ethereum", "bnb", "polygon"}:
                continue
            for contract in {
                t.metadata.get("transaction_to")
                for t in outgoing
                if t.to_addr in targets
                and t.metadata.get("transaction_to")
                and t.metadata["transaction_to"] != t.token_address
            }:
                profile = await registry.get(key[0]).get_profile(contract)
                if profile.is_contract and not LabelService.lookup(
                    db, key[0], contract, case.scenario_id
                ):
                    contract_groups[(key[0], contract)].append(key)
        for (_, contract), keys in contract_groups.items():
            if len(set(keys)) < 2:
                continue
            for key in keys:
                uf.union(keys[0], key)
                connections[key].append(
                    {"signal": "shared_sweep_contract", "address": contract, "probabilistic": True}
                )
        old_clusters = list(db.scalars(select(Cluster.id).where(Cluster.created_from == run.id)))
        if old_clusters:
            db.execute(delete(ClusterMember).where(ClusterMember.cluster_id.in_(old_clusters)))
            db.execute(delete(Cluster).where(Cluster.id.in_(old_clusters)))
        candidate_signals, candidate_hops = defaultdict(dict), {}
        for group in uf.groups():
            labels = [
                (key, label)
                for key in group
                for label in LabelService.lookup(db, *key, case.scenario_id)
            ]
            verified_entities = {
                label.entity_id for _, label in labels if label.source == "verified"
            }
            if len(verified_entities) > 1:
                raise AppError(
                    "CONFLICTING_CLUSTER_LABELS",
                    "Verified cluster labels conflict; review required",
                    409,
                )
            best = next((label for _, label in labels if label.source == "verified"), None)
            if not best and len({label.entity_id for _, label in labels}) == 1:
                best = max((label for _, label in labels), key=lambda label: label.confidence)
            cluster = Cluster(
                id=uuid.uuid4(),
                chain=group[0][0],
                entity_id=best.entity_id if best else None,
                kind="sweep_funder_ownership",
                created_from=run.id,
            )
            db.add(cluster)
            db.flush()
            for key in group:
                db.add(
                    ClusterMember(cluster_id=cluster.id, address=key[1], evidence=connections[key])
                )
                if key in lookup:
                    lookup[key].cluster_id = cluster.id
            entities = {label.entity_id for _, label in labels}
            hub_nodes = []
            for key in group:
                if key not in histories:
                    continue
                incoming, outgoing = histories[key]
                depositors = [
                    other[1]
                    for other, found in sweeps.items()
                    if other[0] == key[0] and any(s["target"] == key[1] for s in found)
                ]
                for sender in sorted({t.from_addr for t in incoming})[:30]:
                    if sender not in depositors:
                        adapter = registry.get(key[0])
                        found = detect_sweeps(
                            await adapter.get_transfers(sender, "in"),
                            await adapter.get_transfers(sender, "out"),
                        )
                        if any(s["target"] == key[1] for s in found):
                            depositors.append(sender)
                metrics = hub_metrics(incoming, outgoing, depositors)
                if metrics["validated"]:
                    hub_nodes.append(key)
                    if key in lookup:
                        lookup[key].role = "hub_candidate"
                        lookup[key].evidence_json = [
                            *lookup[key].evidence_json,
                            {"signal": "hub_validation", **metrics},
                        ]
                for probe in ProbeService.match_hub(db, *key):
                    entity_id = uuid.UUID(probe["entity_id"])
                    entities.add(entity_id)
                    candidate_signals[entity_id]["probe_map_match"] = {
                        "signal": "probe_map_match",
                        "weight": probe["weight"],
                        **{k: probe[k] for k in ["matching_sweeps", "freshness", "source"]},
                        "chain": key[0],
                        "address": key[1],
                    }
            if best:
                cluster.entity_id = best.entity_id
            elif len(entities) == 1:
                cluster.entity_id = next(iter(entities))
            for entity_id in entities:
                signals = candidate_signals[entity_id]
                own_labels = [(key, label) for key, label in labels if label.entity_id == entity_id]
                if verified_entities and entity_id not in verified_entities:
                    continue
                for key, label in own_labels:
                    verified = label.source == "verified"
                    name = (
                        "federated_confirmation"
                        if verified
                        else "exact_public_label"
                        if label.source == "public"
                        else "cluster_label"
                    )
                    weight = (
                        0.95
                        if verified
                        else decayed_weight(
                            min(0.7, label.confidence),
                            label.last_confirmed_at,
                            now(),
                            label.decay_tau_days,
                        )
                    )
                    signal = {
                        "signal": name,
                        "weight": weight,
                        "address": key[1],
                        "chain": key[0],
                        "label_id": str(label.id),
                        "source": label.source,
                        "as_of": label.last_confirmed_at.isoformat(),
                        "verified": verified,
                        "evidence": label.evidence_json,
                    }
                    if weight > signals.get(name, {}).get("weight", -1):
                        signals[name] = signal
                repeated = [s for key in group for s in sweeps.get(key, [])]
                if repeated:
                    signals["sweep_pattern"] = {
                        "signal": "sweep_pattern",
                        "weight": 0.5,
                        "repeated_sweeps": sum(s["count"] for s in repeated),
                        "source": "simulated" if case.scenario_id else "observed",
                    }
                if hub_nodes:
                    signals["hub_validated"] = {
                        "signal": "hub_validated",
                        "weight": 0.3,
                        "addresses": [key[1] for key in hub_nodes],
                    }
                if any(e["signal"] == "shared_funder" for key in group for e in connections[key]):
                    signals["shared_funder"] = {
                        "signal": "shared_funder",
                        "weight": 0.4,
                        "probabilistic": True,
                    }
                if any(
                    e["signal"] == "shared_sweep_contract"
                    for key in group
                    for e in connections[key]
                ):
                    signals["shared_sweep_contract"] = {
                        "signal": "shared_sweep_contract",
                        "weight": 0.4,
                        "probabilistic": True,
                    }
                reachable = [
                    lookup[key].hop
                    for key in group
                    if key in lookup
                    and (key in hub_nodes or any(k == key for k, label in own_labels))
                ]
                hop = min(
                    reachable,
                    default=min((lookup[key].hop for key in group if key in lookup), default=0),
                )
                candidate_hops[entity_id] = min(candidate_hops.get(entity_id, hop), hop)
                if best:
                    cluster.entity_id = best.entity_id
        calibration = (
            db.scalar(select(CalibrationMap).order_by(CalibrationMap.created_at.desc()))
            if case.scenario_id
            else None
        )
        candidates = []
        for entity_id, signals_by_name in candidate_signals.items():
            signals = list(signals_by_name.values())
            verified = any(s.get("verified") for s in signals)
            raw = noisy_or(signals, verified)
            score = (
                raw
                if verified or not calibration
                else min(0.9, calibrate(raw, calibration.bins_json))
            )
            entity = db.get(Entity, entity_id)
            candidates.append(
                {
                    "entity_id": str(entity_id),
                    "entity": entity.name,
                    "entity_type": entity.type,
                    "confidence": score,
                    "raw_confidence": raw,
                    "hops": candidate_hops.get(entity_id),
                    "evidence": signals,
                    "verified": verified,
                    "source": "simulated" if case.scenario_id else entity.source,
                }
            )
        candidates.sort(key=lambda c: (c["verified"], c["confidence"]), reverse=True)
        winner = candidates[0] if candidates else None
        if (
            winner
            and len(candidates) > 1
            and candidates[1]["verified"]
            and winner["entity_id"] != candidates[1]["entity_id"]
        ):
            raise AppError(
                "CONFLICTING_VASP_CONFIRMATIONS",
                "More than one verified entity was reached; review required",
                409,
            )
        result = db.scalar(select(Attribution).where(Attribution.trace_run_id == run.id))
        if not result:
            result = Attribution(case_id=case_id, trace_run_id=run.id)
            db.add(result)
        result.entity_id = uuid.UUID(winner["entity_id"]) if winner else None
        result.confidence = winner["confidence"] if winner else 0
        result.hops = winner["hops"] if winner else None
        result.evidence_json = (
            winner["evidence"]
            if winner
            else [
                {
                    "signal": "insufficient_entity_evidence",
                    "note": "Sweep behaviour alone cannot identify an exchange",
                }
            ]
        )
        if calibration and winner and not winner["verified"]:
            result.evidence_json = [
                *result.evidence_json,
                {
                    "signal": "isotonic_calibration",
                    "map_id": str(calibration.id),
                    "fitted_on": calibration.fitted_on,
                    "raw_confidence": winner["raw_confidence"],
                },
            ]
        result.candidates_json = candidates
        result.status = "confirmed" if winner and winner["verified"] else "inferred"
        result.source = "simulated" if case.scenario_id else "observed"
        case.status = "ATTRIBUTED" if result.confidence >= 0.6 else "OPEN"
        for node in nodes:
            if node.label_id:
                label = db.get(Label, node.label_id)
                entity = db.get(Entity, label.entity_id)
                if entity.type in {"mixer", "bridge", "swap"}:
                    node.role = "swap_service" if entity.type == "swap" else entity.type
            if (
                winner
                and node.role == "hub_candidate"
                and node.cluster_id
                and db.get(Cluster, node.cluster_id).entity_id == result.entity_id
            ):
                node.role = "hot_wallet"
            if node.cluster_id and winner:
                cluster = db.get(Cluster, node.cluster_id)
                if any(
                    str(s.get("cluster_id", "")) == str(cluster.id) for s in winner["evidence"]
                ) or any(n.cluster_id == cluster.id and n.role == "hot_wallet" for n in nodes):
                    cluster.entity_id = result.entity_id
        for node in nodes:
            if node.cluster_id:
                cluster = db.get(Cluster, node.cluster_id)
                if cluster.entity_id:
                    node.evidence_json = [
                        e
                        for e in node.evidence_json
                        if e.get("signal") != "cluster_label_propagation"
                    ] + [
                        {
                            "signal": "cluster_label_propagation",
                            "entity_id": str(cluster.entity_id),
                            "cluster_id": str(cluster.id),
                            "probabilistic": True,
                        }
                    ]
                    if node.role == "unknown":
                        node.role = "exchange_cluster"
        db.commit()
        audit(
            actor.id if actor else None,
            "case.attributed",
            {"case_id": str(case_id), "status": result.status, "confidence": result.confidence},
            db=db,
        )
        return result

    @staticmethod
    def latest(db, case_id):
        run = latest_trace(db, case_id, completed=True)
        row = db.scalar(select(Attribution).where(Attribution.trace_run_id == run.id))
        if not row:
            raise AppError("ATTRIBUTION_NOT_FOUND", "Run attribution first", 404)
        return row

    @staticmethod
    def train_calibration(db):
        samples = []
        case_ids = []
        for result, case, truth in db.execute(
            select(Attribution, Case, ScenarioTruth)
            .join(Case, Attribution.case_id == Case.id)
            .join(ScenarioTruth, Case.scenario_id == ScenarioTruth.scenario_id)
        ).all():
            if result.status == "confirmed" or not result.candidates_json:
                continue
            raw = result.candidates_json[0]["raw_confidence"]
            samples.append((raw, str(result.entity_id) == truth.truth_json["entity_id"]))
            case_ids.append(str(case.id))
        if len(samples) < 3:
            raise AppError(
                "INSUFFICIENT_CALIBRATION_DATA",
                "Attribute at least three synthetic cases first",
                422,
            )
        bins = fit_isotonic(samples)
        row = CalibrationMap(
            method="isotonic", bins_json=bins, fitted_on=f"simulated_cases:{len(set(case_ids))}"
        )
        db.add(row)
        db.commit()
        return {
            "id": str(row.id),
            "bins": bins,
            "samples": len(samples),
            "source": "simulated",
            "evidence": [
                {
                    "signal": "synthetic_ground_truth_calibration",
                    "case_ids": case_ids,
                    "note": "Training fit; held-out evaluation belongs to hardening",
                }
            ],
        }


def attribution_json(db, row):
    entity = db.get(Entity, row.entity_id) if row.entity_id else None
    return {
        "id": str(row.id),
        "case_id": str(row.case_id),
        "trace_run_id": str(row.trace_run_id),
        "entity_id": str(row.entity_id) if entity else None,
        "entity": entity.name if entity else None,
        "confidence": row.confidence,
        "hops": row.hops,
        "status": row.status,
        "source": row.source,
        "evidence": row.evidence_json,
        "candidates": row.candidates_json,
        "federated_lookup_recommended": row.confidence < 0.6,
    }

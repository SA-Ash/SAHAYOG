import math
import random
import uuid
from collections import Counter, defaultdict

from sqlalchemy import func, select

from app.core.audit import AuditService
from app.core.errors import AppError
from app.models.entities import Case, now
from app.models.intelligence import (
    Attribution,
    Entity,
    GraphEdge,
    GraphNode,
    Scenario,
    ScenarioTruth,
    TransferRecord,
)
from app.models.workflow import (
    AuditLog,
    Forecast,
    ForecastModel,
    FreezeRequest,
    GangExitHistory,
    RequestEvent,
    TaintRun,
)
from app.services.cases import CaseService
from app.services.traces import latest_trace


def quantile(values, probability):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


class ForecastService:
    @staticmethod
    def path(db, scenario):
        truth = db.get(ScenarioTruth, scenario.id).truth_json
        rows = list(
            db.scalars(
                select(TransferRecord)
                .where(TransferRecord.scenario_id == scenario.id)
                .order_by(TransferRecord.block_time, TransferRecord.tx_hash)
            )
        )
        chain, address = truth["victims"][0]["chain"], truth["victims"][0]["suspect_address"]
        time = next(t.block_time for t in rows if t.tx_hash == truth["victims"][0]["tx_hash"])
        states = ["unknown"]
        delays = []
        seen = set()
        for _ in range(40):
            if (chain, address) in seen:
                break
            seen.add((chain, address))
            candidates = [
                t
                for t in rows
                if t.chain == chain and t.from_addr == address and t.block_time >= time
            ]
            if not candidates:
                break
            t = max(candidates, key=lambda t: (int(t.amount), -t.block_time.timestamp()))
            next_chain, target = t.chain, t.to_addr
            protocol = t.metadata_json.get("bridge") or t.metadata_json.get("swap")
            state = (
                "bridge"
                if "bridge" in t.metadata_json
                else "swap_service"
                if "swap" in t.metadata_json
                else "mixer"
                if target == truth.get("mixer_address")
                else "deposit_candidate"
                if target == truth["deposit_address"]
                else "exit:" + truth["entity_id"]
                if target == truth["hub_address"]
                else "unknown"
            )
            states.append(state)
            delays.append(max(1, (t.block_time - time).total_seconds()))
            if protocol:
                states.append("unknown")
                delays.append(20)
                next_chain, target = protocol["destination_chain"], protocol["recipient"]
            chain, address, time = next_chain, target, t.block_time
            if state.startswith("exit:") or state == "mixer":
                break
        return states, delays

    @staticmethod
    def train(db, actor):
        scenarios = list(db.scalars(select(Scenario).order_by(Scenario.seed, Scenario.id)))
        if len(scenarios) < 6:
            raise AppError(
                "INSUFFICIENT_FORECAST_DATA",
                "Generate at least six scenarios covering multiple exits for held-out training",
                422,
            )
        holdout = [s for index, s in enumerate(scenarios) if index % 3 == 2]
        training = [s for s in scenarios if s not in holdout]
        counts, delay_samples = defaultdict(Counter), defaultdict(list)
        for scenario in training:
            states, delays = ForecastService.path(db, scenario)
            for a, b, delay in zip(states, states[1:], delays):
                counts[a][b] += 1
                delay_samples[a + "->" + b].append(delay)
        transitions = {
            a: {b: count / sum(values.values()) for b, count in values.items()}
            for a, values in counts.items()
        }
        if not any(b.startswith("exit:") for values in transitions.values() for b in values):
            raise AppError(
                "FORECAST_EXITS_MISSING",
                "Training scenarios must contain traceable exchange exits",
                422,
            )
        wins = 0
        evaluated = 0
        for scenario in holdout:
            states, _ = ForecastService.path(db, scenario)
            expected = next((s for s in states if s.startswith("exit:")), None)
            if not expected:
                continue
            counts_out = ForecastService.rollouts(
                transitions, delay_samples, "unknown", 500, scenario.seed
            )[0]
            predicted = counts_out.most_common(1)[0][0] if counts_out else None
            wins += predicted == expected
            evaluated += 1
        evaluation = {
            "held_out_top1_accuracy": wins / evaluated if evaluated else None,
            "held_out_cases": evaluated,
            "training_scenario_ids": [str(s.id) for s in training],
            "holdout_scenario_ids": [str(s.id) for s in holdout],
            "split": "Every third sorted scenario held out",
            "source": "simulated",
            "note": "Synthetic evaluation only; not evidence of predictive production accuracy.",
        }
        row = ForecastModel(
            version=(db.scalar(select(func.max(ForecastModel.version))) or 0) + 1,
            trained_on=f"simulated_paths:{len(training)}",
            transition_matrix_json=transitions,
            delay_params_json=dict(delay_samples),
            evaluation_json=evaluation,
        )
        db.add(row)
        db.flush()
        AuditService.append(
            db,
            actor.id,
            "forecast.model_trained",
            {"model_id": str(row.id), "evaluation": evaluation},
        )
        db.commit()
        return {
            "id": str(row.id),
            "version": row.version,
            "transitions": transitions,
            "evaluation": evaluation,
            "disclaimer": "Trained on simulated paths",
        }

    @staticmethod
    def rollouts(transitions, delays, start, count, seed):
        rng = random.Random(seed)
        exits, durations, next_hops = Counter(), [], Counter()
        unresolved = 0
        for _ in range(count):
            state, elapsed = start, 0
            for hop in range(40):
                options = transitions.get(state, {})
                if not options:
                    unresolved += 1
                    break
                target = rng.choices(list(options), weights=list(options.values()))[0]
                if hop == 0:
                    next_hops[target] += 1
                elapsed += rng.choice(delays.get(state + "->" + target, [60]))
                state = target
                if state.startswith("exit:"):
                    exits[state] += 1
                    durations.append(elapsed)
                    break
                if state == "mixer":
                    unresolved += 1
                    break
            else:
                unresolved += 1
        return exits, durations, next_hops, unresolved

    @staticmethod
    def latest(db, case_id):
        row = db.scalar(
            select(Forecast).where(Forecast.case_id == case_id).order_by(Forecast.created_at.desc())
        )
        if not row:
            raise AppError("FORECAST_NOT_FOUND", "Run a forecast first", 404)
        return row

    @staticmethod
    def run(db, case_id, data, actor):
        case = CaseService.get_case(db, case_id)
        trace = latest_trace(db, case_id, completed=True)
        model = db.scalar(select(ForecastModel).order_by(ForecastModel.version.desc()))
        if not model:
            raise AppError(
                "FORECAST_MODEL_MISSING",
                "An admin must train the synthetic forecast model first",
                422,
            )
        nodes = list(db.scalars(select(GraphNode).where(GraphNode.trace_run_id == trace.id)))
        edges = list(db.scalars(select(GraphEdge).where(GraphEdge.trace_run_id == trace.id)))
        from_ids = {e.from_node for e in edges}
        terminal = [
            n
            for n in nodes
            if n.id not in from_ids
            and n.role not in {"victim", "mixer", "hot_wallet", "exchange", "hub_candidate"}
        ]
        if not terminal:
            terminal = [
                n
                for n in nodes
                if n.role not in {"victim", "mixer", "hot_wallet", "exchange", "hub_candidate"}
            ]
            terminal = sorted(terminal, key=lambda n: n.hop, reverse=True)[:1]
        if not terminal:
            raise AppError(
                "FORECAST_FRONTIER_MISSING", "No uncertain frontier is available to forecast", 422
            )
        counts, durations, hops, unresolved = Counter(), [], Counter(), 0
        frontier = []
        for index, node in enumerate(terminal[:20]):
            start = node.role if node.role in model.transition_matrix_json else "unknown"
            exits, times, next_hops, missing = ForecastService.rollouts(
                model.transition_matrix_json,
                model.delay_params_json,
                start,
                data.rollouts,
                data.seed + index,
            )
            counts.update(exits)
            durations.extend(times)
            hops.update(next_hops)
            unresolved += missing
            frontier.append(
                {
                    "node_id": str(node.id),
                    "role": start,
                    "address": node.address,
                    "chain": node.chain,
                    "next_hops": {
                        role: number / data.rollouts for role, number in next_hops.items()
                    },
                }
            )
        total = data.rollouts * len(frontier)
        entities = {state.removeprefix("exit:"): number / total for state, number in counts.items()}
        prior = Counter()
        if case.gang_case_id:
            for result, other in db.execute(
                select(Attribution, Case)
                .join(Case, Attribution.case_id == Case.id)
                .where(
                    Case.gang_case_id == case.gang_case_id,
                    Case.id != case.id,
                    Attribution.status == "confirmed",
                )
            ):
                if not result.entity_id:
                    continue
                if not db.scalar(
                    select(GangExitHistory).where(
                        GangExitHistory.case_id == other.id,
                        GangExitHistory.entity_id == result.entity_id,
                    )
                ):
                    confirmed_at = next(
                        (
                            entry.at
                            for entry in db.scalars(
                                select(AuditLog)
                                .where(AuditLog.action == "case.attributed")
                                .order_by(AuditLog.at)
                            )
                            if entry.payload_json.get("case_id") == str(other.id)
                            and entry.payload_json.get("status") == "confirmed"
                        ),
                        now(),
                    )
                    taint = db.scalar(
                        select(TaintRun)
                        .where(TaintRun.case_id == other.id)
                        .order_by(TaintRun.created_at.desc())
                    )
                    db.add(
                        GangExitHistory(
                            gang_case_id=case.gang_case_id,
                            case_id=other.id,
                            entity_id=result.entity_id,
                            exit_time=confirmed_at,
                            amount=taint.result_json["by_case"].get(str(other.id), "0")
                            if taint
                            else "0",
                        )
                    )
            db.flush()
            history = list(
                db.scalars(
                    select(GangExitHistory).where(
                        GangExitHistory.gang_case_id == case.gang_case_id,
                        GangExitHistory.case_id != case.id,
                        GangExitHistory.exit_time <= now(),
                    )
                )
            )
            if len({r.case_id for r in history}) >= 3:
                for row in history:
                    prior[str(row.entity_id)] += math.exp(
                        -max(0, (now() - row.exit_time).total_seconds()) / (90 * 86400)
                    )
        weight = min(0.75, sum(prior.values()) / (sum(prior.values()) + 10)) if prior else 0
        keys = set(entities) | set(prior)
        reachable_probability = sum(entities.values())
        probabilities = []
        for entity_id in keys:
            entity = db.get(Entity, uuid.UUID(entity_id))
            gang_probability = (
                (prior[entity_id] + 1) / (sum(prior.values()) + len(keys)) if prior else None
            )
            adjusted = (
                (1 - weight) * entities.get(entity_id, 0)
                + weight * gang_probability * reachable_probability
                if prior
                else entities.get(entity_id, 0)
            )
            probabilities.append(
                {
                    "entity_id": entity_id,
                    "entity": entity.name,
                    "global_probability": entities.get(entity_id, 0),
                    "gang_probability": gang_probability,
                    "probability": adjusted,
                }
            )
        probabilities.sort(key=lambda r: r["probability"], reverse=True)
        result = {
            "source": "simulated",
            "disclaimer": "Trained on simulated paths. Forecasts are uncertain and cannot authorize or send requests.",
            "trace_run_id": str(trace.id),
            "rollouts_per_frontier": data.rollouts,
            "seed": data.seed,
            "frontiers": frontier,
            "exit_entity_probs": probabilities,
            "unresolved_probability": unresolved / total,
            "eta_quantiles_seconds": {
                "p10": quantile(durations, 0.1),
                "p50": quantile(durations, 0.5),
                "p90": quantile(durations, 0.9),
            },
            "gang_blend_weight": weight,
            "model_version": model.version,
            "evaluation": model.evaluation_json,
            "evidence": [
                {
                    "signal": "empirical_markov_monte_carlo",
                    "max_hops": 40,
                    "eta_condition": "Conditional on rollouts reaching an exit",
                    "frontier_policy": "Observed nonterminal leaves; last nonterminal address if the trace already reached a known exit",
                }
            ],
        }
        row = Forecast(case_id=case_id, model_id=model.id, result_json=result)
        db.add(row)
        db.flush()
        AuditService.append(
            db,
            actor.id,
            "forecast.generated",
            {"case_id": str(case_id), "forecast_id": str(row.id)},
        )
        db.commit()
        return {"id": str(row.id), **result}

    @staticmethod
    def prestage(db, case_id, actor):
        forecast = ForecastService.latest(db, case_id)
        result = forecast.result_json
        if not result["exit_entity_probs"]:
            raise AppError("FORECAST_EXIT_MISSING", "No exit can be pre-staged", 422)
        frontier = result["frontiers"][0]
        row = FreezeRequest(
            case_id=case_id,
            kind="DISCLOSURE",
            target_address=frontier["address"],
            target_chain=frontier["chain"],
            created_by=actor.id,
            amount_by_case_json={},
            impact_level="UNASSESSED",
            package_json={
                "forecast_only": True,
                "forecast_id": str(forecast.id),
                "suggested_exit": result["exit_entity_probs"][0],
                "source": "simulated",
                "note": "Draft only; replace with an observed impact package before proposal.",
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
                note="Forecast-only draft; dispatch blocked",
            )
        )
        AuditService.append(
            db,
            actor.id,
            "forecast.request_prestaged",
            {"case_id": str(case_id), "request_id": str(row.id)},
        )
        db.commit()
        return {"request_id": str(row.id), "state": "DRAFT", "sent": False}

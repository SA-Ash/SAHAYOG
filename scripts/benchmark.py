import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import select
from app.core.db import SessionLocal
from app.models.entities import Base, User
from app.models.intelligence import Job, ScenarioTruth
from app.models.monitoring import BenchmarkRun
from app.schemas.intelligence import ScenarioInput, TraceParams
from app.services.attribution import AttributionService
from app.services.benchmarks import evaluate
from app.services.cases import CaseService
from app.schemas.cases import CaseCreate, TransactionInput
from app.services.jobs import execute_job
from app.services.probes import seed_probes
from app.services.scenarios import ScenarioGenerator
from app.services.traces import TraceService


async def run(count, output):
    with SessionLocal() as db:
        Base.metadata.create_all(db.get_bind())
        actor = db.scalar(select(User).where(User.role == "INVESTIGATOR"))
        if not actor:
            actor = User(
                name="Benchmark",
                email="benchmark@invalid.test",
                role="INVESTIGATOR",
                password_hash="disabled",
                org_unit="Benchmark",
            )
            db.add(actor)
            db.commit()
        from app.services.demo import seed_directory

        seed_directory(db)
        seed_probes(db)
        samples = []
        for i in range(20):
            scenario = ScenarioGenerator.generate(
                db,
                ScenarioInput(
                    seed=70000 + i,
                    preset="easy" if i % 2 else "hard",
                    hops=2,
                    noise_level=1,
                ),
            )
            case = ScenarioGenerator.load(db, scenario, actor)[0]
            trace = TraceService.start(db, case.id, TraceParams(), actor)
            await TraceService.run(db, trace, TraceParams())
            result = await AttributionService.run(db, case.id, actor)
            truth = db.get(ScenarioTruth, scenario.id).truth_json
            samples.append(
                (result.confidence, int(str(result.entity_id) == truth["entity_id"]))
            )
        scenario = ScenarioGenerator.generate(
            db, ScenarioInput(seed=80000, preset="easy", hops=1, noise_level=0)
        )
        prototype = ScenarioGenerator.load(db, scenario, actor)[0]
        source = prototype.transactions[0]
        transaction = TransactionInput(
            chain=source.chain,
            tx_hash=source.tx_hash,
            victim_address=source.victim_address,
            suspect_address=source.suspect_address,
            token=source.token,
            amount=source.amount,
            decimals=source.decimals,
            tx_time=source.tx_time,
        )
        jobs = []
        for i in range(count):
            case = CaseService.create_case(
                db,
                CaseCreate(title=f"Queue benchmark {i}", transaction=transaction),
                actor,
            )
            case.scenario_id = scenario.id
            db.commit()
            trace = TraceService.start(db, case.id, TraceParams(max_hops=2), actor)
            jobs.append(trace.job_id)
        durations = []
        start = time.perf_counter()
        for job_id in jobs:
            t = time.perf_counter()
            await execute_job(job_id)
            durations.append(time.perf_counter() - t)
        elapsed = time.perf_counter() - start
        db.expire_all()
        failures = sum(db.get(Job, j).status != "COMPLETED" for j in jobs)
        public = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/submission/public_addresses.json"
            ).read_text()
        )
        report = {
            "synthetic_attribution": evaluate(samples),
            "queue": {
                "cases": count,
                "failures": failures,
                "backend": "local durable job executor, sequential consumer",
                "fixture": "same one-hop synthetic scenario across independent cases",
                "seconds": elapsed,
                "throughput_per_second": count / elapsed,
                "p95_trace_seconds": sorted(durations)[
                    min(len(durations) - 1, int(0.95 * len(durations)))
                ],
                "average_trace_seconds": statistics.mean(durations),
            },
            "public_dataset": {
                "addresses": len(public),
                "snapshot": "2022-11-10",
                "source": public[0]["source"],
                "behavioural_evaluation": "NOT_RUN: requires independent explorer observations and held-out labels; historical wallet list is not live accuracy evidence",
            },
        }
        row = BenchmarkRun(dataset="synthetic-heldout-and-queue", result_json=report)
        db.add(row)
        db.commit()
        Path(output).write_text(json.dumps(report, indent=2))
        print(json.dumps(report["queue"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=1000)
    parser.add_argument("--output", default="docs/submission/benchmark-results.json")
    args = parser.parse_args()
    if not 1 <= args.cases <= 10000:
        parser.error("cases must be between 1 and 10000")
    asyncio.run(run(args.cases, args.output))

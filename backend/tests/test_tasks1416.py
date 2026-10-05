from datetime import datetime, timezone

import pytest
from conftest import sign_in
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.entities import User
from app.models.intelligence import Job
from app.models.monitoring import Alert, DormancyState, FenceProposal
from app.schemas.intelligence import ScenarioInput, TraceParams
from app.schemas.workflow import TaintInput
from app.services.monitoring import MonitorService
from app.services.scenarios import ScenarioGenerator
from app.services.taint import TaintService
from app.services.traces import TraceService


async def setup_case(db):
    actor = db.scalar(select(User).where(User.role == "INVESTIGATOR"))
    scenario = ScenarioGenerator.generate(
        db, ScenarioInput(preset="easy", dormant_days=45, noise_level=0)
    )
    case = ScenarioGenerator.load(db, scenario, actor)[0]
    run = TraceService.start(db, case.id, TraceParams(), actor)
    await TraceService.run(db, run, TraceParams())
    await TaintService.run(db, case.id, TaintInput(), actor)
    return actor, scenario, case


@pytest.mark.asyncio
async def test_fence_victims_dormancy_and_cursor(db, monkeypatch):
    monkeypatch.setattr("app.services.jobs.dispatch", lambda _: None)
    actor, scenario, case = await setup_case(db)
    with pytest.raises(AppError, match="Victim"):
        MonitorService.add(
            db, case.id, case.transactions[0].chain, case.transactions[0].victim_address, actor
        )
    MonitorService.add(
        db, case.id, case.transactions[0].chain, case.transactions[0].suspect_address, actor
    )
    end = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await MonitorService.poll(db, case.id, actor, end)
    wakes = db.scalars(select(Alert).where(Alert.kind == "DORMANT_WAKE")).all()
    assert wakes
    assert wakes[0].payload_json["quiet_days"] >= 45
    assert int(wakes[0].payload_json["pre_wake_tainted_amount"]) > 0
    assert wakes[0].payload_json["retrace_job_id"]
    job = db.get(Job, __import__("uuid").UUID(wakes[0].payload_json["retrace_job_id"]))
    assert job.payload_json["refresh_taint"]
    count = db.scalar(select(func.count()).select_from(Alert))
    await MonitorService.poll(db, case.id, actor, end)
    assert db.scalar(select(func.count()).select_from(Alert)) == count
    assert db.scalars(select(DormancyState)).all()
    assert db.scalars(select(FenceProposal)).all()


@pytest.mark.asyncio
async def test_monitor_routes_and_roles(db, client):
    actor, scenario, case = await setup_case(db)
    sign_in(client, "supervisor")
    assert (
        client.post(
            f"/api/v1/cases/{case.id}/fence/members",
            json={"chain": "tron", "address": case.transactions[0].suspect_address},
        ).status_code
        == 403
    )
    sign_in(client)
    assert client.get(f"/api/v1/cases/{case.id}/fence").status_code == 200
    assert (
        client.post(
            f"/api/v1/cases/{case.id}/dormancy/threshold", json={"threshold_days": 0}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/cases/{case.id}/dormancy/threshold",
            json={"threshold_days": 12, "poll_seconds": 20},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/version").json()["version"] == "0.16.0"
    assert client.get("/api/v1/benchmarks").status_code == 200
    assert (
        client.get(
            f"/api/v1/addresses/tron/{case.transactions[0].suspect_address}/activity?case_id={case.id}"
        ).status_code
        == 200
    )


def test_calibration_is_held_out_and_bootstrap_reproducible():
    from app.services.benchmarks import evaluate

    samples = [(i / 100, int(i >= 50)) for i in range(100)]
    report = evaluate(samples)
    assert report == evaluate(samples)
    assert report["training_samples"] == 50
    assert report["heldout"]["samples"] == 50
    assert report["calibrated_heldout"]["ece"] <= report["heldout"]["ece"]
    assert report["bootstrap_95_ci"]["precision"] == [1, 1]

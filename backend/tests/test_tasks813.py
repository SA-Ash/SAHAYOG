import json
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from test_tasks27 import run_case

from app.core.audit import AuditService, digest
from app.core.errors import AppError
from app.engines.seals import merkle_proof, merkle_root, verify_proof
from app.engines.taint import Ledger, allocate
from app.models.entities import Case, User, now
from app.models.intelligence import Label
from app.models.workflow import BlastRadiusReport, FreezeRequest, Report
from app.schemas.intelligence import ScenarioInput
from app.schemas.workflow import DraftInput, ForecastInput, ReportInput, TaintInput, TargetInput
from app.services.forecast import ForecastService
from app.services.gangs import GangService
from app.services.impact import ImpactService, classify_impact
from app.services.reports import ReportService
from app.services.requests import TRANSITIONS, RequestService
from app.services.scenarios import ScenarioGenerator
from app.services.taint import TaintService


@given(
    st.lists(st.integers(min_value=1, max_value=10**12), min_size=1, max_size=20),
    st.integers(min_value=0, max_value=10**15),
)
@settings(max_examples=75, suppress_health_check=[HealthCheck.too_slow])
def test_exact_allocation_conserves_units(weights, amount):
    allocated = allocate(amount, {str(index): weight for index, weight in enumerate(weights)})
    assert sum(allocated.values()) == amount


@given(
    st.lists(
        st.tuples(st.integers(0, 7), st.integers(0, 7), st.integers(1, 10**15)),
        min_size=1,
        max_size=50,
    )
)
@settings(max_examples=50)
def test_random_time_ordered_ledgers_conserve_flows(transfers):
    for method in ["haircut", "fifo", "poison"]:
        ledger = Ledger(method, Decimal(".02"))
        ledger.credit(
            ("ethereum", "0", "USDT", None, 6),
            {"victim-a": 10**16, "victim-b": 10**16, None: 10**16},
        )
        for index, (source, target, amount) in enumerate(transfers):
            ledger.move(
                ("ethereum", str(source), "USDT", None, 6),
                ("ethereum", str(target), "USDT", None, 6),
                amount,
                now() + timedelta(seconds=index),
                str(index),
            )
            for balance in ledger.result():
                assert sum(map(int, balance["by_case"].values())) + int(
                    balance["uncoloured"]
                ) == int(balance["total_balance"])


@pytest.mark.asyncio
async def test_multi_victim_exact_truth_gang_and_restitution(db):
    actor, scenario, case, trace = await run_case(
        db, ScenarioInput(preset="multi-victim", noise_level=0)
    )
    result = await TaintService.run(db, case.id, TaintInput(), actor)
    assert result["traceable_amount"] == "14000000000"
    assert result["uncoloured_amount"] == "9000000000"
    assert len(result["by_case"]) == 5
    assert sum(map(int, result["by_case"].values())) == 14000000000
    assert all(int(r["min"]) <= int(r["max"]) for r in result["ranges"].values())
    assert case.gang_case_id
    gang = GangService.detail(db, case.gang_case_id)
    assert len(gang["cases"]) == 5
    split = GangService.split(db, case.gang_case_id, "10000000001")
    assert sum(map(int, split["by_case"].values())) == 10000000001
    assert split["preview_only"]
    assert GangService.communities(db, case.gang_case_id)["communities"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"chain": "ethereum", "with_bridge": True},
        {"chain": "ethereum", "with_swap": True},
        {"mixer": True},
    ],
)
async def test_taint_protocols_and_mixer_uncertainty(db, options):
    actor, scenario, case, trace = await run_case(db, ScenarioInput(noise_level=0, **options))
    result = await TaintService.run(db, case.id, TaintInput(), actor)
    if options.get("mixer"):
        assert result["traceable_amount"] == "0"
        assert int(result["untraceable_amount"]) > 0
    else:
        assert result["traceable_amount"] == "23000000000"
        assert result["target"]["token"] == "USDC"
    for account in result["balances"]:
        assert sum(map(int, account["by_case"].values())) + int(account["uncoloured"]) == int(
            account["total_balance"]
        )


@pytest.mark.parametrize(
    "share,depositors,swept,kind,hub,expected",
    [
        (Decimal(".5"), 3, False, "personal", False, "LOW"),
        (Decimal(".3"), 3, False, "personal", False, "MEDIUM"),
        (Decimal(".7"), 4, False, "personal", False, "MEDIUM"),
        (Decimal(".09"), 1, False, "personal", False, "HIGH"),
        (Decimal(".9"), 21, False, "personal", False, "HIGH"),
        (Decimal(".9"), 1, False, "pooled", False, "HIGH"),
        (Decimal(".9"), 1, True, "personal", False, "BLOCKED"),
        (Decimal(".9"), 1, False, "personal", True, "BLOCKED"),
    ],
)
def test_all_impact_rule_rows(share, depositors, swept, kind, hub, expected):
    assert classify_impact(share, depositors, swept, kind, hub)[0] == expected


@pytest.mark.asyncio
async def test_swept_target_hard_block_and_disclosure_draft(db):
    actor, scenario, case, trace = await run_case(db)
    await TaintService.run(db, case.id, TaintInput(), actor)
    impact = await ImpactService.compute(db, case.id, TargetInput(), actor)
    assert impact["swept"] and not impact["freeze_allowed"]
    with pytest.raises(AppError) as exc:
        RequestService.draft(db, case.id, DraftInput(), actor)
    assert exc.value.code == "FREEZE_BLOCKED"
    row = RequestService.draft(db, case.id, DraftInput(kind="DISCLOSURE"), actor)
    assert row.state == "DRAFT" and row.amount_by_case_json == {}


class Portal:
    def __init__(self, state="FROZEN"):
        self.sent = []
        self.state = state

    async def submit_request(self, payload):
        self.sent.append(payload)
        return {"id": payload["request_key"], "state": self.state}

    async def get_request(self, rid):
        return {"id": rid, "state": self.state}


async def draft_package(db):
    actor, scenario, case, trace = await run_case(db)
    await TaintService.run(db, case.id, TaintInput(), actor)
    await ImpactService.compute(db, case.id, TargetInput(), actor)
    row = RequestService.draft(db, case.id, DraftInput(kind="DISCLOSURE"), actor)
    supervisor = db.scalar(select(User).where(User.role == "SUPERVISOR"))
    return actor, supervisor, case, row


@pytest.mark.asyncio
async def test_two_keys_elevation_state_machine_dispatch_and_audit(db):
    actor, supervisor, case, row = await draft_package(db)
    reason = (
        "Reviewed victim evidence and impact; disclosure is required for account investigation."
    )
    RequestService.act(db, row.id, "propose", actor, reason)
    with pytest.raises(AppError):
        RequestService.act(db, row.id, "approve", supervisor, reason)
    actor.role = "SUPERVISOR"
    with pytest.raises(AppError) as exc:
        RequestService.act(db, row.id, "approve", actor, reason, True)
    assert exc.value.code == "SAME_USER_TWO_KEYS"
    actor.role = "INVESTIGATOR"
    RequestService.act(db, row.id, "approve", supervisor, reason, True)
    portal = Portal()
    await RequestService.dispatch(db, row.id, client=portal)
    assert row.state == "ACKNOWLEDGED" and len(portal.sent) == 1
    await RequestService.dispatch(db, row.id, client=portal)
    assert len(portal.sent) == 1
    assert AuditService.verify(db)["valid"]
    for state, legal in TRANSITIONS.items():
        for target in set(TRANSITIONS) - legal:
            invalid = FreezeRequest(id=uuid.uuid4(), state=state)
            with pytest.raises(AppError):
                RequestService.transition(db, invalid, target, actor, "illegal")


@pytest.mark.asyncio
async def test_golden_hour_expires_and_release_is_delivered(db):
    actor, supervisor, case, row = await draft_package(db)
    impact = db.get(BlastRadiusReport, uuid.UUID(row.package_json["impact_id"]))
    impact.result_json = {
        **impact.result_json,
        "freeze_allowed": True,
        "swept": False,
        "is_hub": False,
        "amount_by_case": {str(case.id): "1000000"},
    }
    impact.traceable_amount = "1000000"
    impact.est_balance = "1000000"
    from app.models.intelligence import Vasp

    row.target_vasp_id = db.scalar(select(Vasp.id))
    row.package_json = {**row.package_json, "impact": impact.result_json}
    row.kind = "FREEZE"
    row.amount_by_case_json = {str(case.id): "1000000"}
    row.impact_level = "LOW"
    db.commit()
    RequestService.act(
        db,
        row.id,
        "golden-hour",
        actor,
        "Urgent temporary hold pending a separate supervisor confirmation",
        minutes=1,
    )
    portal = Portal()
    await RequestService.dispatch(db, row.id, client=portal)
    assert row.state == "FROZEN"
    row.second_key_due = now() - timedelta(seconds=1)
    row.expires_at = row.second_key_due
    db.commit()
    assert str(row.id) in await RequestService.expire(db, portal)
    assert row.state == "EXPIRED"
    assert portal.sent[-1]["action"] == "RELEASE"
    with pytest.raises(AppError):
        RequestService.act(
            db,
            row.id,
            "confirm",
            supervisor,
            "Second key arrives too late to confirm the hold",
            True,
        )


def test_audit_detects_payload_and_tail_tampering(db):
    row = AuditService.append(db, None, "test.audit", {"evidence": "original"})
    db.commit()
    assert AuditService.verify(db)["valid"]
    db.execute(
        text("UPDATE audit_log SET payload_json=:payload WHERE id=:id"),
        {"payload": json.dumps({"evidence": "altered"}), "id": row.id},
    )
    db.commit()
    db.expire_all()
    assert not AuditService.verify(db)["valid"]


def test_merkle_inclusion_and_tamper():
    leaves = [digest({"evidence": i}) for i in range(7)]
    root = merkle_root(leaves)
    for index, leaf in enumerate(leaves):
        assert verify_proof(leaf, merkle_proof(leaves, index), root)
        assert not verify_proof("0" * 64, merkle_proof(leaves, index), root)


@pytest.mark.asyncio
async def test_sealed_pdf_snapshot_json_and_public_verification(db, tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    actor, scenario, case, trace = await run_case(db, ScenarioInput(preset="easy", noise_level=0))
    await TaintService.run(db, case.id, TaintInput(), actor)
    await ImpactService.compute(db, case.id, TargetInput(), actor)
    result = ReportService.build(db, case.id, ReportInput(), actor)
    report = db.get(Report, uuid.UUID(result["id"]))
    assert Path(report.pdf_path).read_bytes().startswith(b"%PDF")
    assert ReportService.verify(db, result["bundle_hash"])["valid"]
    proof = ReportService.proof(db, report.id, uuid.UUID(result["items"][0]["id"]))
    assert verify_proof(proof["leaf"], proof["path"], proof["root"])
    label = db.scalar(select(Label))
    label.evidence_json = [{"later": "changed"}]
    db.commit()
    assert ReportService.verify(db, result["bundle_hash"])["valid"]
    bundle = json.loads(json.dumps(report.bundle_json))
    bundle["title"] = "Altered"
    assert not ReportService.verify(db, result["bundle_hash"], bundle)["valid"]
    assert "case_id" not in ReportService.verify(db, result["bundle_hash"])
    output = Path("/tmp/sahyog-813-report.pdf")
    output.write_bytes(Path(report.pdf_path).read_bytes())


@pytest.mark.asyncio
async def test_farms_operator_similarity_and_private_collisions(db):
    actor, scenario, case, trace = await run_case(db, ScenarioInput(farm_size=12, noise_level=0))
    farms = await GangService.farms(db, case.id)
    assert farms[0]["members"] == 12 and farms[0]["status"] == "suggested"
    approved = GangService.approve_farm(db, uuid.UUID(farms[0]["id"]), actor)
    assert approved["status"] == "approved"
    network = GangService.communities(db, uuid.UUID(approved["gang_case_id"]))
    assert all(
        farms[0]["evidence"]["chain"] + ":" + member in {node["id"] for node in network["nodes"]}
        for member in farms[0]["evidence"]["members"]
    )
    _, _, other, _ = await run_case(db, ScenarioInput(seed=43, noise_level=0))
    assert GangService.similar(db, case.id)
    other.created_by = db.scalar(select(User.id).where(User.role == "SUPERVISOR"))
    db.get(User, other.created_by).org_unit = "Second unit"
    shared = __import__("app.models.intelligence", fromlist=["GraphNode"]).GraphNode
    node = db.scalar(
        select(shared).where(shared.trace_run_id == trace.id, shared.role == "unknown")
    )
    other_trace = __import__("app.services.traces", fromlist=["latest_trace"]).latest_trace(
        db, other.id, completed=True
    )
    other_node = db.scalar(
        select(shared).where(shared.trace_run_id == other_trace.id, shared.role == "unknown")
    )
    other.scenario_id = case.scenario_id
    other_node.address = node.address
    db.commit()
    notices = GangService.collisions(db, actor)
    assert notices and "case_b" not in notices[0]
    room = GangService.open_room(db, uuid.UUID(notices[0]["id"]), actor)
    assert len(room["units"]) == 2
    outsider = db.scalar(select(User).where(User.role == "ADMIN"))
    outsider.org_unit = "Third unit"
    db.commit()
    with pytest.raises(AppError):
        GangService.open_room(db, uuid.UUID(notices[0]["id"]), outsider)


@pytest.mark.asyncio
async def test_forecast_held_out_training_reproducibility_and_draft_only(db):
    actor, scenario, case, trace = await run_case(db)
    for seed in range(100, 108):
        ScenarioGenerator.generate(db, ScenarioInput(seed=seed, noise_level=0, hops=2))
    admin = db.scalar(select(User).where(User.role == "ADMIN"))
    trained = ForecastService.train(db, admin)
    evaluation = trained["evaluation"]
    assert set(evaluation["training_scenario_ids"]).isdisjoint(evaluation["holdout_scenario_ids"])
    result = ForecastService.run(db, case.id, ForecastInput(rollouts=100), actor)
    repeat = ForecastService.run(db, case.id, ForecastInput(rollouts=100), actor)
    assert result["exit_entity_probs"] == repeat["exit_entity_probs"]
    assert (
        abs(
            sum(r["probability"] for r in result["exit_entity_probs"])
            + result["unresolved_probability"]
            - 1
        )
        < 0.000001
    )
    assert result["eta_quantiles_seconds"]["p10"] <= result["eta_quantiles_seconds"]["p90"]
    draft = ForecastService.prestage(db, case.id, actor)
    assert not draft["sent"]
    with pytest.raises(AppError) as exc:
        RequestService.act(
            db,
            uuid.UUID(draft["request_id"]),
            "propose",
            actor,
            "Attempt to send a speculative forecast-only request",
        )
    assert exc.value.code == "FORECAST_ONLY"


async def unswept_package(db):
    from sqlalchemy import delete

    from app.models.intelligence import GraphEdge, RoutingDecision, ScenarioTruth, TransferRecord

    actor, scenario, case, trace = await run_case(db, ScenarioInput(noise_level=0))
    truth = db.get(ScenarioTruth, scenario.id).truth_json
    deposit = truth["deposit_address"]
    records = list(
        db.scalars(select(TransferRecord).where(TransferRecord.scenario_id == scenario.id))
    )
    incoming = [record for record in records if record.to_addr == deposit]
    main = max(incoming, key=lambda record: int(record.amount))
    removed = [
        record.id
        for record in records
        if record.from_addr == deposit or (record.to_addr == deposit and record.id != main.id)
    ]
    db.execute(delete(GraphEdge).where(GraphEdge.transfer_id.in_(removed)))
    db.execute(delete(TransferRecord).where(TransferRecord.id.in_(removed)))
    db.add(
        RoutingDecision(
            case_id=case.id,
            target_vasp_id=uuid.UUID(truth["entity_id"]),
            channel="DIRECT_PORTAL",
            issuer_target="TETHER",
            reason_json=[],
            ranking_json=[],
        )
    )
    db.commit()
    await TaintService.run(db, case.id, TaintInput(), actor)
    impact = await ImpactService.compute(db, case.id, TargetInput(), actor)
    assert impact["freeze_allowed"] and not impact["swept"]
    supervisor = db.scalar(select(User).where(User.role == "SUPERVISOR"))
    return actor, supervisor, case, main


@pytest.mark.asyncio
async def test_freeze_reservations_release_and_new_outgoing_preflight(db):
    from app.models.intelligence import TransferRecord

    actor, supervisor, case, incoming = await unswept_package(db)
    first = RequestService.draft(
        db, case.id, DraftInput(kind="FREEZE", amount="15000000000"), actor
    )
    second = RequestService.draft(
        db, case.id, DraftInput(kind="FREEZE", amount="15000000000"), actor
    )
    reason = "Reviewed exact victim amounts, target balance and ownership evidence before action."
    for row in [first, second]:
        RequestService.act(db, row.id, "propose", actor, reason)
    RequestService.act(db, first.id, "approve", supervisor, reason, True)
    with pytest.raises(AppError) as exc:
        RequestService.act(db, second.id, "approve", supervisor, reason, True)
    assert exc.value.code == "FUNDS_ALREADY_RESERVED"
    db.rollback()
    portal = Portal()
    await RequestService.dispatch(db, first.id, client=portal)
    assert first.state == "FROZEN" and first.issuer_request_id
    RequestService.act(db, first.id, "release", supervisor, reason, True)
    await RequestService.dispatch(db, first.id, "release", portal)
    assert [payload["action"] for payload in portal.sent[-2:]] == ["RELEASE", "RELEASE"]
    RequestService.act(db, second.id, "approve", supervisor, reason, True)
    db.add(
        TransferRecord(
            chain=incoming.chain,
            tx_hash="a" * 64,
            log_index=0,
            block_time=now(),
            from_addr=incoming.to_addr,
            to_addr=incoming.from_addr,
            amount="1",
            token=incoming.token,
            decimals=incoming.decimals,
            token_address=incoming.token_address,
            scenario_id=case.scenario_id,
            metadata_json={},
            source="synthetic",
        )
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        await RequestService.preflight(db, second)
    assert exc.value.code == "STALE_BALANCE"


@pytest.mark.asyncio
async def test_golden_second_key_extends_both_holds(db):
    actor, supervisor, case, incoming = await unswept_package(db)
    row = RequestService.draft(db, case.id, DraftInput(kind="FREEZE", amount="1000000"), actor)
    reason = "Urgent hold based on reviewed victim evidence, followed by independent confirmation."
    RequestService.act(db, row.id, "golden-hour", actor, reason, minutes=5)
    portal = Portal()
    await RequestService.dispatch(db, row.id, client=portal)
    initial_expiry = row.expires_at
    RequestService.act(db, row.id, "confirm", supervisor, reason, True)
    await RequestService.dispatch(db, row.id, "extend", portal)
    assert row.expires_at > initial_expiry and row.approver_id == supervisor.id
    assert [payload["action"] for payload in portal.sent[-2:]] == ["EXTEND", "EXTEND"]
    assert AuditService.verify(db)["valid"]


def test_report_numbers_survive_javascript_roundtrip():
    from app.services.reports import report_numbers

    payload = report_numbers(
        {"confidence": 1.0, "duration": 15.0, "fraction": 0.123, "amount": "9007199254740993"}
    )
    assert digest(payload) == digest(
        {"confidence": 1, "duration": 15, "fraction": 0.123, "amount": "9007199254740993"}
    )


@pytest.mark.asyncio
async def test_gang_exit_history_requires_three_priors_and_normalizes(db):
    from app.models.intelligence import Entity
    from app.models.workflow import GangExitHistory

    actor, scenario, case, trace = await run_case(
        db, ScenarioInput(preset="multi-victim", noise_level=0)
    )
    await TaintService.run(db, case.id, TaintInput(), actor)
    for seed in range(200, 208):
        ScenarioGenerator.generate(db, ScenarioInput(seed=seed, noise_level=0))
    admin = db.scalar(select(User).where(User.role == "ADMIN"))
    ForecastService.train(db, admin)
    related = list(db.scalars(select(Case).where(Case.gang_case_id == case.gang_case_id)))
    entity = db.scalar(select(Entity))
    for other in [other for other in related if other.id != case.id][:2]:
        db.add(
            GangExitHistory(
                gang_case_id=case.gang_case_id,
                case_id=other.id,
                entity_id=entity.id,
                exit_time=now(),
                amount="1",
            )
        )
    db.commit()
    assert (
        ForecastService.run(db, case.id, ForecastInput(rollouts=100), actor)["gang_blend_weight"]
        == 0
    )
    third = next(
        other
        for other in related
        if other.id != case.id
        and not db.scalar(select(GangExitHistory.id).where(GangExitHistory.case_id == other.id))
    )
    db.add(
        GangExitHistory(
            gang_case_id=case.gang_case_id,
            case_id=third.id,
            entity_id=entity.id,
            exit_time=now(),
            amount="1",
        )
    )
    db.commit()
    result = ForecastService.run(db, case.id, ForecastInput(rollouts=100), actor)
    assert result["gang_blend_weight"] > 0
    assert all(exit["gang_probability"] > 0 for exit in result["exit_entity_probs"])
    assert (
        abs(
            sum(exit["probability"] for exit in result["exit_entity_probs"])
            + result["unresolved_probability"]
            - 1
        )
        < 0.000001
    )


def test_concurrent_audit_append_and_business_writes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.entities import Base
    from app.models.intelligence import Setting

    engine = create_engine(
        "sqlite:///" + str(tmp_path / "audit.db"),
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    with Session(engine) as session:
        AuditService.append(session, None, "test.initial", {})
        session.commit()

    def append(index):
        with Session(engine) as session:
            if index % 2:
                session.add(Setting(key="concurrent:" + str(index), value={"index": index}))
                session.flush()
            AuditService.append(session, None, "test.concurrent", {"index": index})
            session.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))
    with Session(engine) as session:
        assert AuditService.verify(session)["checked"] == 25
        assert AuditService.verify(session)["valid"]
    engine.dispose()

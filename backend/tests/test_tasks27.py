import json
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from conftest import sign_in
from sqlalchemy import func, select

from app.adapters.bitcoin import BitcoinAdapter
from app.adapters.evm import EvmAdapter
from app.adapters.http import HttpCache, cache_key
from app.adapters.registry import AdapterRegistry
from app.adapters.storage import store_transfers
from app.adapters.tron import TronAdapter, tron_address, tron_hex
from app.core.config import get_settings
from app.core.errors import AppError
from app.engines.signals import decayed_weight, fit_isotonic, noisy_or
from app.models.entities import User, now
from app.models.intelligence import (
    FederatedReply,
    GraphNode,
    HotwalletCluster,
    ScenarioTruth,
    Setting,
    TransferRecord,
)
from app.schemas.intelligence import ScenarioInput, TraceParams
from app.services.attribution import AttributionService
from app.services.demo import demo_address, demo_hub
from app.services.federated import FederatedLookupService
from app.services.labels import LabelService
from app.services.privacy import multiply, point, salted_hash, scalar
from app.services.probes import ProbeService, seed_probes
from app.services.routing import RoutingService
from app.services.scenarios import ScenarioGenerator
from app.services.traces import TraceService, graph_json


async def run_case(db, params=None):
    actor = db.scalar(select(User).where(User.role == "INVESTIGATOR"))
    scenario = ScenarioGenerator.generate(db, params or ScenarioInput())
    case = ScenarioGenerator.load(db, scenario, actor)[0]
    trace = TraceService.start(db, case.id, TraceParams(), actor)
    await TraceService.run(db, trace, TraceParams())
    return actor, scenario, case, trace


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["easy", "hard", "multi-victim"])
async def test_scenario_trace_attribution_ground_truth(db, preset):
    actor, scenario, case, trace = await run_case(db, ScenarioInput(preset=preset))
    truth = db.get(ScenarioTruth, scenario.id).truth_json
    graph = graph_json(db, trace)
    assert truth["deposit_address"] in {n["data"]["address"] for n in graph["nodes"]}
    assert truth["hub_address"] in {n["data"]["address"] for n in graph["nodes"]}
    before = db.scalar(select(func.count()).select_from(TransferRecord))
    assert ScenarioGenerator.generate(db, ScenarioInput(preset=preset)).id == scenario.id
    assert before == db.scalar(select(func.count()).select_from(TransferRecord))
    assert TraceService.start(db, case.id, TraceParams(), actor).id == trace.id
    seed_probes(db)
    result = await AttributionService.run(db, case.id, actor)
    assert str(result.entity_id) == truth["entity_id"]
    assert result.confidence == 0.9
    assert result.status == "inferred"
    assert any(e["signal"] == "probe_map_match" for e in result.evidence_json)
    assert len(ScenarioGenerator.load(db, scenario, actor)) == (
        5 if preset == "multi-victim" else 1
    )


@pytest.mark.asyncio
async def test_synthetic_labels_are_scoped_and_noise_not_attributed(db):
    actor = db.scalar(select(User).where(User.role == "INVESTIGATOR"))
    easy = ScenarioGenerator.generate(db, ScenarioInput(preset="easy"))
    hard = ScenarioGenerator.generate(db, ScenarioInput(preset="hard"))
    hub = db.get(ScenarioTruth, easy.id).hub_address
    assert LabelService.lookup(db, "tron", hub, easy.id)
    assert not LabelService.lookup(db, "tron", hub, hard.id)
    actor, scenario, case, trace = await run_case(db)
    result = await AttributionService.run(db, case.id, actor)
    assert result.entity_id is None
    assert result.confidence == 0
    adapter = AdapterRegistry(db, scenario.id).get("tron")
    noise = demo_address("tron", str(scenario.id) + ":noise-0")
    transfers = await adapter.get_transfers(noise)
    assert len(transfers) == 1
    assert not ProbeService.match_hub(db, "tron", noise)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options,kind",
    [
        ({"peel_chain_len": 6}, "PEEL"),
        ({"split_merge_intensity": 2}, "LAYERING"),
        ({"chain": "ethereum", "with_bridge": True}, "bridge"),
        ({"chain": "ethereum", "with_swap": True}, "swap_service"),
        ({"mixer": True}, "mixer"),
    ],
)
async def test_patterns_bridge_swap_and_mixer_stop(db, options, kind):
    actor, scenario, case, trace = await run_case(db, ScenarioInput(**options))
    from app.models.intelligence import PatternFinding

    if kind in {"PEEL", "LAYERING"}:
        assert kind in set(
            db.scalars(select(PatternFinding.kind).where(PatternFinding.trace_run_id == trace.id))
        )
    else:
        assert kind in set(
            db.scalars(select(GraphNode.role).where(GraphNode.trace_run_id == trace.id))
        )
    if kind == "mixer":
        assert trace.stop_summary_json["mixer_uncertain"] == 1
        assert db.get(ScenarioTruth, scenario.id).truth_json["per_case_amounts"] == {"0": "0"}
    if kind == "bridge":
        assert {"ethereum", "polygon"} <= set(
            db.scalars(select(GraphNode.chain).where(GraphNode.trace_run_id == trace.id))
        )


@pytest.mark.asyncio
async def test_trace_limits_and_hash_only_resolution(db):
    actor, scenario, case, trace = await run_case(db)
    tx = case.transactions[0]
    tx.suspect_address = None
    tx.victim_address = None
    tx.token = None
    tx.amount = None
    db.commit()
    params = TraceParams(max_hops=2)
    run = TraceService.start(db, case.id, params, actor)
    await TraceService.run(db, run, params)
    assert tx.suspect_address and tx.amount and tx.tx_time.utcoffset() is not None
    assert run.stop_summary_json["hop_limit"] >= 1
    assert TraceService.start(db, case.id, params, actor).id == run.id
    assert max(n["data"]["hop"] for n in graph_json(db, run)["nodes"]) <= 2


@pytest.mark.asyncio
async def test_cache_replay_no_network_outage_fallback_and_upsert(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    monkeypatch.setattr(settings, "redis_url", "")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"payload": "recorded"})

    db.add(Setting(key="replay_mode", value={"enabled": False}))
    db.commit()
    cache = HttpCache(
        db, "fixture", "https://fixture.invalid", transport=httpx.MockTransport(handle)
    )
    payload, source = await cache.request("/record", {"z": 2, "a": 1, "apikey": "private"})
    assert source == "live"
    assert len(calls) == 1
    assert cache_key("fixture", "/record", {"a": 1, "z": 2}) == cache_key(
        "fixture", "/record", {"z": 2, "a": 1, "apikey": "other"}
    )
    row = db.get(Setting, "replay_mode")
    row.value = {"enabled": True}
    db.commit()
    assert (await cache.request("/record", {"a": 1, "z": 2}))[1] == "cache"
    assert len(calls) == 1
    with pytest.raises(AppError) as exc:
        await cache.request("/missing")
    assert exc.value.code == "REPLAY_CACHE_MISS"
    assert len(calls) == 1
    row.value = {"enabled": False}
    db.commit()
    monkeypatch.setattr(settings, "cache_ttl_seconds", 0)

    def fail(request):
        raise httpx.ConnectError("offline")

    cache.transport = httpx.MockTransport(fail)
    assert (await cache.request("/record", {"a": 1, "z": 2}))[0] == payload
    actor, scenario, case, trace = await run_case(db)
    transfer = (
        await AdapterRegistry(db, scenario.id).get("tron").get_tx(case.transactions[0].tx_hash)
    )[0]
    count = db.scalar(select(func.count()).select_from(TransferRecord))
    store_transfers(db, [transfer, transfer], scenario.id)
    assert db.scalar(select(func.count()).select_from(TransferRecord)) == count


class FixtureHttp:
    def __init__(self, chain):
        self.data = json.loads((Path(__file__).parent / "fixtures" / f"{chain}.json").read_text())

    async def request(self, path, params=None, method="GET", body=None):
        if "action" in (params or {}):
            action = params["action"]
            key = action + ":" + params["data"] if action == "eth_call" else action
        elif path == "/wallet/triggerconstantcontract":
            key = body["function_selector"]
        else:
            key = path
        return self.data[key], "cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("chain", ["ethereum", "bnb", "polygon", "tron", "bitcoin"])
async def test_recorded_adapter_contract(db, chain):
    provider = "ethereum" if chain in {"ethereum", "bnb", "polygon"} else chain
    http = FixtureHttp(provider)
    adapter = (
        EvmAdapter(db, chain, http)
        if provider == "ethereum"
        else TronAdapter(db, http)
        if chain == "tron"
        else BitcoinAdapter(db, http)
    )
    txs = await adapter.get_tx("a" * 64)
    assert txs
    assert all(
        t.chain == chain and t.source == "cache" and t.block_time.utcoffset() is not None
        for t in txs
    )
    assert txs[-1].amount == "123456789"
    assert all(isinstance(t.amount, str) for t in txs)
    if chain == "tron":
        assert tron_address(tron_hex(txs[0].from_addr)) == txs[0].from_addr


def test_unsupported_chain_confidence_decay_and_psi(db):
    with pytest.raises(AppError) as exc:
        AdapterRegistry(db).get("solana")
    assert exc.value.code == "UNSUPPORTED_CHAIN"
    assert noisy_or([{"signal": "label", "weight": 0.7}, {"signal": "probe", "weight": 0.8}]) == 0.9
    assert noisy_or([{"signal": "verified", "weight": 0.95}], True) == 0.95
    assert decayed_weight(0.7, now() - timedelta(days=180), now(), 180) < 0.3
    assert fit_isotonic([(0.2, 1), (0.3, 0), (0.8, 1)])[0]["probability"] == 0.5
    chain, address = "tron", demo_hub("bharat", "tron")
    assert salted_hash("a" * 64, chain, address) != salted_hash("b" * 64, chain, address)
    a, b = scalar(), scalar()
    raw = point(chain, address)
    assert multiply(a, multiply(b, raw)) == multiply(b, multiply(a, raw))
    with pytest.raises(ValueError):
        multiply(a, "00" * 32)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hash", "psi"])
async def test_federation_privacy_confirmation_routing_and_verified_override(
    db, tmp_path, monkeypatch, mode
):
    from mock_services.vasps import main as mock
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///" + str(tmp_path / "vasps.db"), connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(mock, "engine", engine)
    mock.Base.metadata.create_all(engine)
    monkeypatch.setenv("MOCK_VASP_DELAY_SCALE", "0")
    actor, scenario, case, trace = await run_case(db)
    query = FederatedLookupService.start(db, case.id, mode, actor)
    captured = []
    transport = httpx.ASGITransport(app=mock.app)

    class RecordingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            captured.append(request.content.decode())
            return await transport.handle_async_request(request)

    await FederatedLookupService.run(db, query, RecordingTransport())
    positive = list(
        db.scalars(
            select(FederatedReply).where(
                FederatedReply.query_id == query.id, FederatedReply.match.is_(True)
            )
        )
    )
    assert len(positive) == 1
    assert all(target["address"] not in body for body in captured for target in query.targets_json)
    assert all("address" not in json.loads(body) for body in captured)
    result = AttributionService.latest(db, case.id)
    assert result.status == "confirmed" and result.confidence >= 0.95
    truth = db.get(ScenarioTruth, scenario.id).truth_json
    assert str(result.entity_id) == truth["entity_id"]
    assert (
        LabelService.lookup(db, truth["chain"], truth["hub_address"], scenario.id)[0].source
        == "verified"
    )
    confirmed = await AttributionService.run(db, case.id, actor)
    assert confirmed.status == "confirmed" and confirmed.confidence >= 0.95
    routing = RoutingService.recommend(db, case.id, actor)
    assert routing["channel"] == "DIRECT_PORTAL"
    assert routing["issuer_target"] == "TETHER"
    assert routing["sent"] is False
    engine.dispose()


@pytest.mark.asyncio
async def test_probe_freshness_and_routing_timeout(db):
    actor, scenario, case, trace = await run_case(db)
    seed_probes(db)
    truth = db.get(ScenarioTruth, scenario.id).truth_json
    match = ProbeService.match_hub(db, "tron", truth["hub_address"])[0]
    assert match["weight"] > 0.78
    row = db.get(HotwalletCluster, uuid.UUID(match["id"]))
    row.last_confirmed = now() - timedelta(days=90)
    db.commit()
    stale = ProbeService.match_hub(db, "tron", truth["hub_address"])[0]
    assert stale["stale"] and stale["weight"] < 0.05


def test_new_route_rbac_and_validation(client):
    sign_in(client, "supervisor")
    assert client.post("/api/v1/dev/scenarios", json={"preset": "hard"}).status_code == 403
    assert client.post("/api/v1/admin/replay-mode", json={"enabled": False}).status_code == 403
    assert (
        client.post("/api/v1/probes/run", json={"entity_id": str(uuid.uuid4())}).status_code == 403
    )
    assert client.get("/api/v1/chain/solana/tx/" + "a" * 64).json()["code"] == "UNSUPPORTED_CHAIN"
    sign_in(client)
    assert (
        client.post(
            "/api/v1/dev/scenarios", json={"chain": "tron", "with_bridge": True}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/dev/scenarios", json={"mixer": True, "mixer_denoms": []}).status_code
        == 422
    )


@pytest.mark.asyncio
async def test_cctp_requires_matching_destination_mint(db):
    from eth_abi import encode

    from app.engines.bridges import (
        CCTP_CONTRACTS,
        CCTP_TOPIC,
        MESSAGE_TOPIC,
        MINT_TOPIC,
        USDC,
        BridgeDecoder,
    )
    from app.schemas.intelligence import Transfer

    sender, recipient, transmitter = [
        demo_address("ethereum", key) for key in ["sender", "recipient", "transmitter"]
    ]

    def word(address):
        return bytes.fromhex(address[2:]).rjust(32, b"\0")

    def topic(address):
        return "0x" + word(address).hex()

    nonce = "0x" + (12).to_bytes(32).hex()
    log = {
        "address": CCTP_CONTRACTS["ethereum"],
        "topics": [CCTP_TOPIC, nonce, topic(USDC["ethereum"]), topic(sender)],
        "data": "0x"
        + encode(
            ["uint256", "bytes32", "uint32", "bytes32", "bytes32"],
            [1000000, word(recipient), 7, word(CCTP_CONTRACTS["polygon"]), b"\0" * 32],
        ).hex(),
    }
    transfer = Transfer(
        chain="ethereum",
        tx_hash="a" * 64,
        log_index=0,
        from_addr=sender,
        to_addr=CCTP_CONTRACTS["ethereum"],
        token="USDC",
        token_address=USDC["ethereum"],
        amount="1000000",
        decimals=6,
        block_time=now(),
        source="cache",
        metadata={"receipt_logs": [log]},
    )
    body = (
        (0).to_bytes(4)
        + word(USDC["ethereum"])
        + word(recipient)
        + (1000000).to_bytes(32)
        + word(sender)
    )
    logs = [
        {
            "address": CCTP_CONTRACTS["polygon"],
            "topics": [MINT_TOPIC, topic(recipient), topic(USDC["polygon"])],
            "data": "0x" + (1000000).to_bytes(32).hex(),
        },
        {
            "address": transmitter,
            "topics": [MESSAGE_TOPIC, topic(sender), nonce],
            "data": "0x"
            + encode(
                ["uint32", "bytes32", "bytes"], [0, word(CCTP_CONTRACTS["ethereum"]), body]
            ).hex(),
        },
    ]
    output = Transfer(
        chain="polygon",
        tx_hash="b" * 64,
        log_index=1,
        from_addr="0x" + "0" * 40,
        to_addr=recipient,
        token="USDC",
        token_address=USDC["polygon"],
        amount="1000000",
        decimals=6,
        block_time=transfer.block_time + timedelta(minutes=2),
        source="cache",
        metadata={"receipt_logs": logs},
    )

    class Destination:
        async def call(self, *args, **kwargs):
            return topic(transmitter), "cache"

        async def get_transfers(self, *args):
            return [output]

    class Registry:
        def get(self, chain):
            return Destination()

    result = await BridgeDecoder.resolve(transfer, None, Registry())
    assert result["destination_chain"] == "polygon" and not result["inferred"]
    assert result["block_time"] == output.block_time
    output.metadata["receipt_logs"][1]["topics"][2] = "0x" + (13).to_bytes(32).hex()
    with pytest.raises(AppError) as exc:
        await BridgeDecoder.resolve(transfer, None, Registry())
    assert exc.value.code == "UNSUPPORTED_BRIDGE"


@pytest.mark.asyncio
async def test_configured_swap_inference_rejects_ambiguous_outputs(db):
    from app.engines.bridges import BridgeDecoder
    from app.schemas.intelligence import SwapServiceInput, Transfer
    from app.services.public_labels import seed_public_labels

    sender, router, output_wallet, recipient = [
        demo_address("ethereum", k) for k in ["input", "swap", "output", "recipient"]
    ]
    service = SwapServiceInput(
        name="Recorded swap fixture",
        chain="ethereum",
        address=router,
        input_token="ETH",
        destination_chain="polygon",
        output_address=output_wallet,
        output_token="USDC",
        rate="2",
        reference="fixture:test",
    )
    db.add(Setting(key="swap_services", value={"services": [service.model_dump(mode="json")]}))
    db.commit()
    transfer = Transfer(
        chain="ethereum",
        tx_hash="c" * 64,
        log_index=0,
        from_addr=sender,
        to_addr=router,
        token="ETH",
        amount="1000000000000000000",
        decimals=18,
        block_time=now(),
        source="cache",
    )
    output = Transfer(
        chain="polygon",
        tx_hash="d" * 64,
        log_index=0,
        from_addr=output_wallet,
        to_addr=recipient,
        token="USDC",
        amount="2000000",
        decimals=6,
        block_time=transfer.block_time + timedelta(seconds=15),
        source="cache",
    )
    outputs = [output]

    class Adapter:
        async def get_transfers(self, *args):
            return outputs

    class Registry:
        scenario_id = None

        def __init__(self, session):
            self.db = session

        def get(self, chain):
            return Adapter()

    result = await BridgeDecoder.resolve(transfer, Adapter(), Registry(db))
    assert result["inferred"] and result["recipient"] == recipient
    outputs.append(output.model_copy(update={"tx_hash": "0x" + "e" * 64}))
    with pytest.raises(AppError):
        await BridgeDecoder.resolve(transfer, Adapter(), Registry(db))
    seed_public_labels(db)
    seed_public_labels(db)
    assert (
        len(LabelService.lookup(db, "ethereum", "0x28c6c06298d514db089934071355e5743bf21d60")) == 1
    )


@pytest.mark.asyncio
async def test_conflicting_vasp_claims_force_review_and_block_rerun(db, monkeypatch):
    actor, scenario, case, trace = await run_case(db)
    query = FederatedLookupService.start(db, case.id, "hash", actor)

    def both_claim(request):
        body = json.loads(request.content)
        claims = any(name in request.url.path for name in ["bharat", "global"])
        return httpx.Response(
            200,
            json={
                "match": claims,
                "matched_hashes": body["hashes"] if claims else [],
                "source": "simulated",
            },
        )

    await FederatedLookupService.run(db, query, httpx.MockTransport(both_claim))
    result = AttributionService.latest(db, case.id)
    assert result.status == "needs_review" and result.confidence == 0
    assert case.status == "OPEN"
    with pytest.raises(AppError) as exc:
        await AttributionService.run(db, case.id, actor)
    assert exc.value.code == "CONFLICTING_VASP_CONFIRMATIONS"
    assert RoutingService.recommend(db, case.id, actor)["channel"] == "MANUAL_REVIEW"


@pytest.mark.asyncio
async def test_routing_uses_destination_asset_and_requires_actual_nonresponse(db):
    from app.models.intelligence import Vasp
    from app.services.demo import demo_id

    actor, scenario, case, trace = await run_case(
        db, ScenarioInput(chain="ethereum", with_swap=True)
    )
    seed_probes(db)
    result = await AttributionService.run(db, case.id, actor)
    result.entity_id = demo_id("entity", "global")
    result.status = "confirmed"
    db.commit()
    routing = RoutingService.recommend(db, case.id, actor)
    assert routing["issuer_target"] == "CIRCLE"
    assert routing["channel"] == "DIRECT_VASP_REQUEST"
    vasp = db.get(Vasp, demo_id("vasp", "global"))
    vasp.attempts = 2
    vasp.response_rate = 0
    db.commit()
    assert RoutingService.recommend(db, case.id, actor)["channel"] == "FIU_LEGAL_ESCALATION"

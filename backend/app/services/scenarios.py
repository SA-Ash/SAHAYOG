import hashlib
import json
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapters.storage import store_profile, store_transfers
from app.models.intelligence import Label, Scenario, ScenarioTruth
from app.schemas.cases import CaseCreate, TransactionInput
from app.schemas.intelligence import AddressProfile, Transfer
from app.services.cases import CaseService
from app.services.demo import DEMO_VASPS, demo_address, demo_hub, demo_id, seed_directory


class ScenarioGenerator:
    @staticmethod
    def generate(db, params):
        values = params.model_dump(mode="json")
        identity = json.dumps(values, sort_keys=True, separators=(",", ":"))
        scenario_id = uuid.uuid5(uuid.NAMESPACE_URL, "sahyog-scenario:" + identity)
        existing = db.get(Scenario, scenario_id)
        if existing:
            return existing
        seed_directory(db)
        scenario = Scenario(
            id=scenario_id, name=params.preset, params_json=values, seed=params.seed
        )
        db.add(scenario)
        db.flush()
        rng = random.Random(params.seed)
        chain = params.chain.value
        slug, name, *_ = DEMO_VASPS[params.seed % 3]
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        if "start_hour" in params.operator_profile:
            base = base.replace(hour=int(params.operator_profile["start_hour"]) % 24)
        hub_chain = (
            "polygon"
            if chain == "ethereum" and params.with_bridge
            else "ethereum"
            if params.with_bridge
            else chain
        )
        hub = demo_hub(slug, hub_chain)
        token = "USDC" if params.with_bridge else "USDT" if chain != "bitcoin" else "BTC"
        decimals = 8 if chain == "bitcoin" else 6
        transfers = []
        profiles = {}
        sequence = 0

        def address(key, selected=chain):
            return demo_address(selected, str(scenario_id) + ":" + key)

        def transfer(sender, receiver, amount, second, selected=chain, asset=token, meta=None):
            nonlocal sequence
            tx_hash = hashlib.sha256(f"{scenario_id}:{sequence}".encode()).hexdigest()
            sequence += 1
            t = Transfer(
                chain=selected,
                tx_hash=tx_hash,
                log_index=0,
                from_addr=sender,
                to_addr=receiver,
                token=asset,
                amount=str(amount),
                decimals=decimals,
                block_time=base + timedelta(seconds=second),
                source="synthetic",
                metadata=meta or {},
            )
            transfers.append(t)
            return t

        amounts = (
            [6000, 4000, 2000, 1000, 1000]
            if params.preset == "multi-victim"
            else [23000 + i * 1000 for i in range(params.victims)]
        )
        amounts = [v * 10**decimals for v in amounts]
        victims = []
        merge = address("merge")
        for i, amount in enumerate(amounts):
            victim, collector = address(f"victim-{i}"), address(f"collector-{i}")
            t = transfer(victim, collector, amount, i)
            transfer(collector, merge, amount, 10 + i)
            victims.append(
                {
                    "victim_address": victim,
                    "suspect_address": collector,
                    "tx_hash": t.tx_hash,
                    "amount": str(amount),
                    "chain": chain,
                    "token": token,
                    "decimals": decimals,
                    "tx_time": t.block_time.isoformat(),
                }
            )
        uncoloured = 9000 * 10**decimals if params.preset == "multi-victim" else 0
        if uncoloured:
            transfer(address("unreported-background"), merge, uncoloured, 20)
        initial_total = sum(amounts) + uncoloured
        current, total, clock = merge, initial_total, 40
        layered = []
        for i in range(params.split_merge_intensity):
            left, right, target = (
                address(f"split-{i}-a"),
                address(f"split-{i}-b"),
                address(f"remerge-{i}"),
            )
            part = total // 3
            transfer(current, left, part, clock)
            transfer(current, right, total - part, clock + 1)
            transfer(left, target, part, clock + 10)
            transfer(right, target, total - part, clock + 11)
            layered.append([current, left, right, target])
            current, clock = target, clock + 20
        peeled = 0
        peel_nodes = []
        for i in range(params.peel_chain_len):
            next_address = address(f"peel-{i}")
            small = max(1, total // 10)
            transfer(current, address(f"peel-exit-{i}"), small, clock)
            transfer(current, next_address, total - small, clock + 1)
            peel_nodes.append(current)
            total -= small
            peeled += small
            current, clock = next_address, clock + 20
        for i in range(params.hops):
            next_address = address(f"hop-{i}")
            transfer(current, next_address, total, clock)
            current = next_address
            clock += max(1, int(rng.lognormvariate(2.5, 0.4)))
        if params.dormant_days:
            profiles[(chain, current)] = {"dormant_days": params.dormant_days}
            clock += params.dormant_days * 86400
        if params.with_bridge:
            bridge = address("bridge")
            destination = address("bridge-recipient", hub_chain)
            transfer(
                current,
                bridge,
                total,
                clock,
                meta={
                    "bridge": {
                        "protocol": "cctp_fixture",
                        "destination_chain": hub_chain,
                        "recipient": destination,
                        "token": token,
                        "amount": str(total),
                        "decimals": decimals,
                        "source": "simulated",
                    }
                },
            )
            current = destination
            clock += 20
        if params.with_swap:
            router = address("swap-router")
            recipient = address("swap-recipient")
            transfer(
                current,
                router,
                total,
                clock,
                meta={
                    "swap": {
                        "protocol": "synthetic_swap",
                        "destination_chain": chain,
                        "recipient": recipient,
                        "token": "USDC",
                        "amount": str(total),
                        "decimals": 6,
                        "source": "simulated",
                    }
                },
            )
            current = recipient
            token = "USDC"
            clock += 20
        mixer_address = None
        if params.mixer:
            mixer_address = address("mixer", hub_chain)
            transfer(current, mixer_address, total, clock, selected=hub_chain, asset=token)
            for i in range(10):
                denomination = int(params.mixer_denoms[i % len(params.mixer_denoms)])
                transfer(
                    address(f"mixer-input-{i}", hub_chain),
                    mixer_address,
                    denomination,
                    clock - 10 + i,
                    selected=hub_chain,
                    asset=token,
                )
                transfer(
                    mixer_address,
                    address(f"mixer-output-{i}", hub_chain),
                    denomination,
                    clock + 30 + i,
                    selected=hub_chain,
                    asset=token,
                )
            current = address("mixer-hidden-recipient", hub_chain)
            transfer(mixer_address, current, total, clock + 50, selected=hub_chain, asset=token)
            clock += 60
        deposit = address("deposit", hub_chain)
        transfer(current, deposit, total, clock, selected=hub_chain, asset=token)
        transfer(deposit, hub, total, clock + 45, selected=hub_chain, asset=token)
        funder = address("exchange-funder", hub_chain)
        deposit_wallets = [deposit] + [address(f"sibling-deposit-{i}", hub_chain) for i in range(5)]
        for i, deposit_wallet in enumerate(deposit_wallets):
            for cycle in range(3):
                value = (5000 + i * 100) * 10**decimals
                at = clock + 200 + cycle * 600 + i * 15
                transfer(
                    address(f"depositor-{i}-{cycle}", hub_chain),
                    deposit_wallet,
                    value,
                    at,
                    selected=hub_chain,
                    asset=token,
                )
                transfer(deposit_wallet, hub, value, at + 30, selected=hub_chain, asset=token)
            profiles[(hub_chain, deposit_wallet)] = {
                "gas_funder": funder if hub_chain != "tron" else None,
                "activated_by": funder if hub_chain == "tron" else None,
            }
        transfer(
            hub,
            address("withdrawal", hub_chain),
            total,
            clock + 100,
            selected=hub_chain,
            asset=token,
        )
        noise_clock = 0
        for i in range(params.noise_level):
            noise_clock += max(1, int(rng.expovariate(1 / 60)))
            transfer(
                address(f"noise-{i}"),
                address(f"noise-target-{i}"),
                rng.randint(1, 100) * 10**decimals,
                noise_clock,
            )
        for i in range(params.farm_size):
            farm = address(f"farm-{i}")
            transfer(address("farm-funder"), farm, 100, clock + 1000 + i)
            profiles[(chain, farm)] = {
                "gas_funder": address("farm-funder") if chain != "tron" else None,
                "activated_by": address("farm-funder") if chain == "tron" else None,
            }
        store_transfers(db, transfers, scenario_id, commit=False)
        addresses = {(t.chain.value, a) for t in transfers for a in [t.from_addr, t.to_addr]}
        for selected, a in sorted(addresses):
            history = [
                t for t in transfers if t.chain == selected and a in {t.from_addr, t.to_addr}
            ]
            extra = profiles.get((selected, a), {})
            evidence = [
                {
                    "signal": "synthetic_profile",
                    "source": "simulated",
                    **{k: v for k, v in extra.items() if k == "dormant_days"},
                }
            ]
            store_profile(
                db,
                AddressProfile(
                    chain=selected,
                    address=a,
                    first_seen=min(t.block_time for t in history),
                    last_seen=max(t.block_time for t in history),
                    tx_count=len(history),
                    is_contract=a == mixer_address
                    or any(
                        t.to_addr == a and ("bridge" in t.metadata or "swap" in t.metadata)
                        for t in history
                    ),
                    source="synthetic",
                    evidence=evidence,
                    **{k: v for k, v in extra.items() if k != "dormant_days"},
                ),
                str(scenario_id),
                commit=False,
            )
        delivered = {
            str(i): str(amount * total // initial_total) for i, amount in enumerate(amounts)
        }
        if delivered:
            delivered[str(len(amounts) - 1)] = str(
                sum(amounts) * total // initial_total
                - sum(int(v) for k, v in delivered.items() if k != str(len(amounts) - 1))
            )
        if params.mixer:
            delivered = {str(i): "0" for i in range(len(amounts))}
        truth = {
            "source": "simulated",
            "chain": hub_chain,
            "deposit_address": deposit,
            "hub_address": hub,
            "entity": name,
            "entity_id": str(demo_id("entity", slug)),
            "victims": victims,
            "per_case_amounts": delivered,
            "untraceable_amount": str(
                sum(amounts) if params.mixer else uncoloured * total // initial_total
            ),
            "peeled_amount": str(peeled),
            "peel_nodes": peel_nodes,
            "layering": layered,
            "mixer_address": mixer_address,
            "operator_profile": params.operator_profile,
        }
        db.add(
            ScenarioTruth(
                scenario_id=scenario_id,
                deposit_address=deposit,
                hub_address=hub,
                entity=name,
                per_case_amounts_json=delivered,
                untraceable_amount=truth["untraceable_amount"],
                truth_json=truth,
            )
        )
        if params.preset == "easy":
            existing_label = db.scalar(
                select(Label).where(
                    Label.chain == hub_chain,
                    Label.address == hub,
                    Label.source == "public",
                    Label.scenario_key == str(scenario_id),
                )
            )
            if not existing_label:
                db.add(
                    Label(
                        chain=hub_chain,
                        address=hub,
                        entity_id=demo_id("entity", slug),
                        source="public",
                        scenario_key=str(scenario_id),
                        confidence=0.7,
                        evidence_json=[{"signal": "synthetic_public_label", "source": "demo_seed"}],
                    )
                )
        db.commit()
        return scenario

    @staticmethod
    def load(db, scenario, actor):
        truth = db.get(ScenarioTruth, scenario.id).truth_json
        cases = []
        for i, victim in enumerate(truth["victims"]):
            ref = f"SIM-{scenario.id.hex[:12]}-{i + 1}"
            transaction = TransactionInput(**victim)
            case = CaseService.create_case(
                db,
                CaseCreate(
                    title=f"{scenario.name} scenario · victim {i + 1}",
                    sahyog_ref=ref,
                    transaction=transaction,
                ),
                actor,
                source="simulated",
                idempotent=True,
            )
            case.scenario_id = scenario.id
            cases.append(case)
        db.commit()
        return cases

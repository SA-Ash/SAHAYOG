import csv
import io
import math
import random
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.audit import audit
from app.core.errors import AppError
from app.models.entities import now
from app.models.intelligence import Entity, HotwalletCluster, ProbeRun
from app.schemas.intelligence import ProbeInput
from app.services.demo import demo_address, demo_hub
from app.services.validation import address_info


class ProbeService:
    @staticmethod
    def record(db, entity_id, chain, deposit, amount, hub, delay, at, mode):
        if not db.get(Entity, entity_id):
            raise AppError("ENTITY_NOT_FOUND", "Entity not found", 404)
        chain = getattr(chain, "value", chain)
        deposit, hub = address_info(chain, deposit)[0], address_info(chain, hub)[0]
        if (
            mode not in {"demo_seed", "simulated", "live_probe"}
            or not str(amount).isdigit()
            or not 0 < int(amount) < 10**38
            or delay < 0
        ):
            raise AppError("INVALID_PROBE", "Invalid probe amount, delay or mode", 422)
        if at.utcoffset() is None:
            raise AppError("INVALID_PROBE", "Probe timestamp requires timezone", 422)
        key = (entity_id, chain, deposit, hub, at)
        row = db.scalar(
            select(ProbeRun).where(
                ProbeRun.entity_id == key[0],
                ProbeRun.chain == key[1],
                ProbeRun.deposit_address == key[2],
                ProbeRun.swept_to == key[3],
                ProbeRun.run_at == key[4],
            )
        )
        if row:
            return row
        row = ProbeRun(
            entity_id=entity_id,
            chain=chain,
            deposit_address=deposit,
            amount=str(int(amount)),
            swept_to=hub,
            sweep_delay_s=delay,
            run_at=at,
            mode=mode,
        )
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def aggregate(db):
        groups = db.execute(
            select(
                ProbeRun.entity_id,
                ProbeRun.chain,
                ProbeRun.swept_to,
                func.count(),
                func.min(ProbeRun.run_at),
                func.max(ProbeRun.run_at),
            ).group_by(ProbeRun.entity_id, ProbeRun.chain, ProbeRun.swept_to)
        ).all()
        for entity, chain, hub, count, first, last in groups:
            row = db.scalar(
                select(HotwalletCluster).where(
                    HotwalletCluster.entity_id == entity,
                    HotwalletCluster.chain == chain,
                    HotwalletCluster.hub_address == hub,
                )
            )
            modes = set(
                db.scalars(
                    select(ProbeRun.mode).where(
                        ProbeRun.entity_id == entity,
                        ProbeRun.chain == chain,
                        ProbeRun.swept_to == hub,
                    )
                )
            )
            source = next(iter(modes)) if len(modes) == 1 else "mixed"
            if not row:
                row = HotwalletCluster(entity_id=entity, chain=chain, hub_address=hub)
                db.add(row)
            row.matching_sweeps, row.first_seen, row.last_confirmed, row.source = (
                count,
                first,
                last,
                source,
            )
        db.commit()

    @staticmethod
    def match_hub(db, chain, address):
        address = address_info(chain, address)[0]
        rows = db.scalars(
            select(HotwalletCluster).where(
                HotwalletCluster.chain == chain, HotwalletCluster.hub_address == address
            )
        ).all()
        return sorted(
            [ProbeService.cluster_json(db, row) for row in rows],
            key=lambda item: item["weight"],
            reverse=True,
        )

    @staticmethod
    def cluster_json(db, row):
        age = max(0, (now() - row.last_confirmed).total_seconds() / 86400)
        freshness = math.exp(-age / 30)
        weight = 0.8 * (1 - math.exp(-row.matching_sweeps / 10)) * freshness
        return {
            "id": str(row.id),
            "entity_id": str(row.entity_id),
            "entity": db.get(Entity, row.entity_id).name,
            "chain": row.chain,
            "hub_address": row.hub_address,
            "matching_sweeps": row.matching_sweeps,
            "first_seen": row.first_seen,
            "last_confirmed": row.last_confirmed,
            "age_days": round(age, 2),
            "freshness": freshness,
            "stale": age > 60,
            "weight": weight,
            "source": row.source,
            "evidence": [
                {
                    "signal": "probe_map_match",
                    "matching_sweeps": row.matching_sweeps,
                    "freshness": freshness,
                    "source": row.source,
                }
            ],
        }

    @staticmethod
    def exchanges(db):
        result = []
        for entity in db.scalars(
            select(Entity).where(Entity.type == "exchange").order_by(Entity.name)
        ):
            clusters = db.scalars(
                select(HotwalletCluster).where(HotwalletCluster.entity_id == entity.id)
            ).all()
            result.append(
                {
                    "id": str(entity.id),
                    "name": entity.name,
                    "source": entity.source,
                    "country": entity.country,
                    "hotwallet_count": len(clusters),
                    "last_probe": max((r.last_confirmed for r in clusters), default=None),
                    "matching_sweeps": sum(r.matching_sweeps for r in clusters),
                }
            )
        return result

    @staticmethod
    def simulate(db, data: ProbeInput, actor):
        entity = db.get(Entity, data.entity_id)
        if not entity or entity.source != "demo_seed":
            raise AppError("SIMULATION_ONLY", "Select a simulated exchange to run a probe", 422)
        vasp = entity.name.split()[1].lower()
        rng = random.Random(data.seed)
        at = now()
        for i in range(data.count):
            ProbeService.record(
                db,
                entity.id,
                data.chain,
                demo_address(
                    data.chain.value, f"probe:{entity.id}:{data.seed}:{i}:{at.isoformat()}"
                ),
                "1000000",
                demo_hub(vasp, data.chain.value),
                rng.randint(15, 90),
                at + timedelta(microseconds=i),
                "simulated",
            )
        ProbeService.aggregate(db)
        audit(
            actor.id, "probe.simulated", {"entity_id": str(entity.id), "count": data.count}, db=db
        )
        return ProbeService.match_hub(db, data.chain, demo_hub(vasp, data.chain.value))

    @staticmethod
    def import_csv(db, raw, actor):
        try:
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            if not rows or len(rows) > 1000:
                raise ValueError()
            parsed = []
            import uuid

            for row in rows:
                entity_id = uuid.UUID(row["entity_id"])
                entity = db.get(Entity, entity_id)
                if not entity:
                    raise ValueError()
                chain = row["chain"]
                deposit = address_info(chain, row["deposit_address"])[0]
                hub = address_info(chain, row["swept_to"])[0]
                amount, delay = str(int(row["amount"])), int(row["sweep_delay_s"])
                at = datetime.fromisoformat(row["run_at"].replace("Z", "+00:00"))
                mode = row.get("mode", "demo_seed")
                if (
                    not 0 < int(amount) < 10**38
                    or delay < 0
                    or at.utcoffset() is None
                    or mode not in {"demo_seed", "live_probe", "simulated"}
                ):
                    raise ValueError()
                parsed.append((entity_id, chain, deposit, amount, hub, delay, at, mode))
        except (ValueError, KeyError, UnicodeError) as exc:
            raise AppError(
                "INVALID_CSV",
                "Expected entity_id,chain,deposit_address,amount,swept_to,sweep_delay_s,run_at,mode",
                422,
            ) from exc
        for row in parsed:
            ProbeService.record(db, *row)
        ProbeService.aggregate(db)
        audit(actor.id, "probe.imported", {"rows": len(parsed)}, db=db)
        return {"imported": len(parsed)}


class RealProbeRunner:
    async def run(self, *args):
        raise AppError(
            "LIVE_PROBING_NOT_CONFIGURED",
            "Live probing requires an approved external integration",
            503,
        )


def seed_probes(db):
    from app.services.demo import DEMO_VASPS, demo_id

    for slug, name, country, registered, delay in DEMO_VASPS[:3]:
        for chain in ["tron", "ethereum", "polygon", "bnb", "bitcoin"]:
            for i in range(40):
                deposit = demo_address(chain, f"seed-probe:{slug}:{i}")
                existing = db.scalar(
                    select(ProbeRun.id).where(
                        ProbeRun.entity_id == demo_id("entity", slug),
                        ProbeRun.chain == chain,
                        ProbeRun.deposit_address == deposit,
                    )
                )
                if not existing:
                    ProbeService.record(
                        db,
                        demo_id("entity", slug),
                        chain,
                        deposit,
                        "1000000",
                        demo_hub(slug, chain),
                        30 + i,
                        now(),
                        "demo_seed",
                    )
    ProbeService.aggregate(db)

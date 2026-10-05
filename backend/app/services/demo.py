import hashlib
import uuid

import base58

DEMO_VASPS = [
    ("bharat", "Demo Bharat Exchange", "IN", True, 0.15),
    ("global", "Demo Global Exchange", "SG", False, 0.35),
    ("europe", "Demo Europe Exchange", "DE", True, 0.6),
    ("negative", "Demo Independent Exchange", "US", False, 0.9),
    ("silent", "Demo Silent Exchange", "KY", False, 8),
]


def demo_id(kind, name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"sahyog-demo:{kind}:{name}")


def demo_address(chain, key):
    raw = hashlib.sha256(key.encode()).digest()[:20]
    if chain == "tron":
        return base58.b58encode_check(b"\x41" + raw).decode()
    if chain == "bitcoin":
        return base58.b58encode_check(b"\x00" + raw).decode()
    return "0x" + raw.hex()


def demo_hub(slug, chain):
    return demo_address(chain, "demo-hub:" + slug)


def seed_directory(db):
    from app.core.config import get_settings
    from app.models.intelligence import Entity, Vasp

    for slug, name, country, registered, delay in DEMO_VASPS:
        entity_id, vasp_id = demo_id("entity", slug), demo_id("vasp", slug)
        if not db.get(Entity, entity_id):
            db.add(
                Entity(
                    id=entity_id,
                    name=name,
                    type="exchange",
                    country=country,
                    vasp_id=vasp_id,
                    source="demo_seed",
                )
            )
            db.flush()
        if not db.get(Vasp, vasp_id):
            db.add(
                Vasp(
                    id=vasp_id,
                    entity_id=entity_id,
                    name=name,
                    country=country,
                    registered_fiu=registered,
                    channel="DIRECT_PORTAL" if country == "IN" else "LEGAL_ESCALATION",
                    endpoint=get_settings().mock_vasp_url + "/vasp/" + slug,
                    request_format={
                        "lookup": "salted-hash-set",
                        "source": "simulated",
                        "delay_seconds": delay,
                    },
                    source="simulated",
                )
            )
    db.commit()

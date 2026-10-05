import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.intelligence import Entity, Label

PUBLIC_LABELS = [
    (
        "Binance",
        "0x28c6c06298d514db089934071355e5743bf21d60",
        "https://www.binance.com/es/blog/community/2895840147147652626",
        "https://etherscan.io/address/0x28c6c06298d514db089934071355e5743bf21d60",
    ),
    (
        "Kraken",
        "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0",
        "https://etherscan.io/address/0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0",
        "https://etherscan.io/address/0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0",
    ),
]


def seed_public_labels(db):
    for name, address, reference, explorer in PUBLIC_LABELS:
        entity_id = uuid.uuid5(uuid.NAMESPACE_URL, "sahyog-public-entity:" + name)
        if not db.get(Entity, entity_id):
            db.add(Entity(id=entity_id, name=name, type="exchange", country="UNK", source="public"))
            db.flush()
        if not db.scalar(
            select(Label).where(
                Label.chain == "ethereum",
                Label.address == address,
                Label.entity_id == entity_id,
                Label.source == "public",
                Label.scenario_key == "live",
            )
        ):
            db.add(
                Label(
                    chain="ethereum",
                    address=address,
                    entity_id=entity_id,
                    source="public",
                    confidence=0.7,
                    last_confirmed_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
                    decay_tau_days=180,
                    evidence_json=[
                        {
                            "signal": "public_address_tag",
                            "reference": reference,
                            "explorer": explorer,
                            "as_of": "2026-06-28",
                            "note": "Public historical address attribution; not a current VASP confirmation",
                        }
                    ],
                )
            )
    db.commit()

import csv
import io
import uuid

from sqlalchemy import select

from app.core.errors import AppError
from app.models.entities import now
from app.models.intelligence import Entity, Label
from app.schemas.intelligence import LabelInput
from app.services.validation import address_info


class LabelService:
    @staticmethod
    def lookup(db, chain, address, scenario_id=None):
        normalized = address_info(chain, address)[0]
        labels = db.scalars(
            select(Label).where(
                Label.chain == chain,
                Label.address == normalized,
                Label.scenario_key == str(scenario_id or "live"),
            )
        ).all()
        return sorted(
            labels,
            key=lambda label: (
                label.source == "verified",
                label.last_confirmed_at,
                label.confidence,
            ),
            reverse=True,
        )

    @staticmethod
    def add(db, data, actor=None):
        if not db.get(Entity, data.entity_id):
            raise AppError("ENTITY_NOT_FOUND", "Entity not found", 404)
        label = db.scalar(
            select(Label).where(
                Label.chain == data.chain,
                Label.address == data.address,
                Label.entity_id == data.entity_id,
                Label.source == data.source,
                Label.scenario_key == "live",
            )
        )
        if not label:
            label = Label(
                chain=data.chain.value,
                address=data.address,
                entity_id=data.entity_id,
                source=data.source,
                evidence_json=data.evidence,
                confidence=data.confidence,
                last_confirmed_at=data.last_confirmed_at or now(),
                decay_tau_days=data.decay_tau_days,
            )
            db.add(label)
        else:
            label.evidence_json = data.evidence
            label.confidence = data.confidence
            label.last_confirmed_at = data.last_confirmed_at or now()
            label.decay_tau_days = data.decay_tau_days
        db.commit()
        return label

    @staticmethod
    def verified(db, chain, address, entity_id, query_id, vasp_id, source, scenario_id=None):
        labels = LabelService.lookup(db, chain, address, scenario_id)
        for existing in labels:
            if existing.source == "verified" and existing.entity_id != entity_id:
                raise AppError(
                    "CONFLICTING_VASP_CONFIRMATIONS",
                    "Different VASPs claimed this address; manual review required",
                    409,
                )
        row = next(
            (
                label
                for label in labels
                if label.entity_id == entity_id and label.source == "verified"
            ),
            None,
        )
        evidence = [
            {
                "signal": "federated_confirmation",
                "query_id": str(query_id),
                "vasp_id": str(vasp_id),
                "source": source,
            }
        ]
        if not row:
            row = Label(
                chain=chain,
                address=address,
                entity_id=entity_id,
                source="verified",
                scenario_key=str(scenario_id or "live"),
                confidence=0.95,
                evidence_json=evidence,
                verified_by=vasp_id,
                decay_tau_days=365,
            )
            db.add(row)
        else:
            row.last_confirmed_at = now()
            row.evidence_json = evidence
        db.commit()
        return row

    @staticmethod
    def import_csv(db, raw, actor):
        try:
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            if len(rows) > 1000:
                raise ValueError("Limit is 1000 labels")
            parsed = []
            for row in rows:
                parsed.append(
                    LabelInput(
                        chain=row["chain"],
                        address=row["address"],
                        entity_id=uuid.UUID(row["entity_id"]),
                        source=row.get("source", "public"),
                        evidence=[{"signal": "csv_import", "reference": row["reference"]}],
                    )
                )
                if not db.get(Entity, parsed[-1].entity_id):
                    raise ValueError("Unknown entity")
        except (KeyError, ValueError, UnicodeError) as exc:
            raise AppError(
                "INVALID_CSV", "Expected chain,address,entity_id,source,reference columns", 422
            ) from exc
        return [LabelService.add(db, data, actor) for data in parsed]


def label_json(db, label):
    entity = db.get(Entity, label.entity_id)
    return {
        "id": str(label.id),
        "chain": label.chain,
        "address": label.address,
        "entity_id": str(label.entity_id),
        "entity": entity.name,
        "entity_type": entity.type,
        "source": label.source,
        "evidence": label.evidence_json,
        "confidence": label.confidence,
        "last_confirmed_at": label.last_confirmed_at,
        "decay_tau_days": label.decay_tau_days,
        "verified": label.source == "verified",
        "data_source": entity.source,
        "scenario_id": label.scenario_key if label.scenario_key != "live" else None,
    }

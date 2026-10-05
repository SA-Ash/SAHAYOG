import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.entities import now
from app.models.workflow import AuditHead, AuditLog


def canonical(value):
    def encode(item):
        if isinstance(item, (UUID, Decimal)):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(type(item).__name__)

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=encode
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class AuditService:
    @staticmethod
    def append(db, actor, action, payload, entity=None, entity_id=None):
        if not db.get(AuditHead, 1):
            try:
                with db.begin_nested():
                    db.add(AuditHead(id=1, sequence=0, entry_hash="0" * 64))
                    db.flush()
            except IntegrityError:
                pass
        db.execute(update(AuditHead).where(AuditHead.id == 1).values(sequence=AuditHead.sequence))
        head = db.scalar(
            select(AuditHead)
            .where(AuditHead.id == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        payload = json.loads(canonical(payload))
        row = AuditLog(
            id=head.sequence + 1,
            actor_id=actor,
            action=action,
            entity=entity or action.split(".")[0],
            entity_id=str(entity_id or payload.get("case_id") or payload.get("request_id") or "")
            or None,
            payload_json=payload,
            payload_hash=digest(payload),
            prev_hash=head.entry_hash,
            at=now(),
        )
        entry = {
            "id": row.id,
            "actor_id": str(actor) if actor else None,
            "action": row.action,
            "entity": row.entity,
            "entity_id": row.entity_id,
            "payload_hash": row.payload_hash,
            "at": row.at.isoformat(),
        }
        row.entry_hash = hashlib.sha256((row.prev_hash + canonical(entry)).encode()).hexdigest()
        db.add(row)
        head.sequence, head.entry_hash = row.id, row.entry_hash
        db.flush()
        return row

    @staticmethod
    def verify(db):
        db.execute(update(AuditHead).where(AuditHead.id == 1).values(sequence=AuditHead.sequence))
        previous, sequence = "0" * 64, 0
        for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
            entry = {
                "id": row.id,
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "action": row.action,
                "entity": row.entity,
                "entity_id": row.entity_id,
                "payload_hash": row.payload_hash,
                "at": row.at.isoformat(),
            }
            expected = hashlib.sha256((previous + canonical(entry)).encode()).hexdigest()
            if (
                row.id != sequence + 1
                or row.prev_hash != previous
                or digest(row.payload_json) != row.payload_hash
                or row.entry_hash != expected
            ):
                return {"valid": False, "first_mismatch": row.id, "checked": sequence}
            previous, sequence = row.entry_hash, row.id
        head = db.get(AuditHead, 1)
        valid = (not head and sequence == 0) or bool(
            head and head.sequence == sequence and head.entry_hash == previous
        )
        return {
            "valid": valid,
            "first_mismatch": None if valid else "head",
            "checked": sequence,
            "head_hash": previous,
        }


def audit(actor, action, payload, db=None):
    if db is not None:
        AuditService.append(db, actor, action, payload)
        db.commit()
    else:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            AuditService.append(session, actor, action, payload)
            session.commit()

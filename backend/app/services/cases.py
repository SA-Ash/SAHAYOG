import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import AppError
from app.models.entities import Case, CaseAttachment, User, VictimTransaction
from app.schemas.cases import CaseCreate, TransactionInput


class CaseService:
    @staticmethod
    def get_case(db: Session, case_id: uuid.UUID):
        case = db.get(Case, case_id)
        if not case:
            raise AppError("CASE_NOT_FOUND", "Case not found", 404)
        return case

    @staticmethod
    def transaction(data: TransactionInput):
        values = data.model_dump(mode="python")
        values["chain"] = data.chain.value
        return VictimTransaction(**values, status="PENDING_RESOLUTION" if not data.suspect_address else "REPORTED")

    @classmethod
    def create_case(cls, db: Session, data: CaseCreate, actor: User, source="manual", idempotent=False):
        if data.sahyog_ref:
            existing = db.scalar(select(Case).where(Case.sahyog_ref == data.sahyog_ref))
            if existing:
                if idempotent:
                    original = existing.transactions[0]
                    fields = data.transaction.model_dump(mode="json")
                    if existing.title != data.title or any(getattr(original, key) != value for key, value in fields.items() if key != "tx_time"):
                        raise AppError("REFERENCE_CONFLICT", "Reference was already received with different complaint data", 409)
                    audit(actor.id, "case.webhook_replayed", {"case_id": str(existing.id)})
                    return existing
                raise AppError("REFERENCE_EXISTS", "A case already uses this complaint reference", 409)
        attachments = []
        if data.attachment_ids and not data.extraction_reviewed:
            raise AppError("REVIEW_REQUIRED", "Review and confirm extracted fields before creating a case", 422)
        for attachment_id in set(data.attachment_ids):
            attachment = db.scalar(select(CaseAttachment).where(CaseAttachment.id == attachment_id).with_for_update())
            if not attachment or attachment.uploaded_by != actor.id or attachment.case_id:
                raise AppError("ATTACHMENT_UNAVAILABLE", "Attachment is not yours or already linked", 403)
            attachments.append(attachment)
        case = Case(title=data.title.strip(), sahyog_ref=data.sahyog_ref, case_ref=f"SHG-{datetime.now(timezone.utc):%Y}-{uuid.uuid4().hex[:12].upper()}", created_by=actor.id, source=source, transactions=[cls.transaction(data.transaction)])
        db.add(case)
        db.flush()
        for attachment in attachments:
            attachment.case_id = case.id
        db.commit()
        db.refresh(case)
        audit(actor.id, "case.created", {"case_id": str(case.id), "source": source})
        return case

    @classmethod
    def attach_victim_tx(cls, db: Session, case_id: uuid.UUID, data: TransactionInput, actor: User):
        case = cls.get_case(db, case_id)
        if case.status == "CLOSED":
            raise AppError("CASE_CLOSED", "Cannot add a transaction to a closed case", 409)
        tx = cls.transaction(data)
        case.transactions.append(tx)
        db.commit()
        db.refresh(case)
        audit(actor.id, "case.victim_transaction_added", {"case_id": str(case.id), "transaction_id": str(tx.id)})
        return case

    @staticmethod
    def list_cases(db: Session, status=None, chain=None, date_from=None, date_to=None, search=None, page=1, page_size=20):
        query = select(Case)
        if status:
            query = query.where(Case.status == status)
        if chain:
            query = query.where(Case.transactions.any(VictimTransaction.chain == chain))
        if date_from:
            query = query.where(Case.created_at >= date_from)
        if date_to:
            query = query.where(Case.created_at < date_to)
        if search:
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(Case.title.ilike(pattern, escape="\\") | Case.case_ref.ilike(pattern, escape="\\") | Case.sahyog_ref.ilike(pattern, escape="\\"))
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = db.scalars(query.order_by(Case.created_at.desc(), Case.id).offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": items, "total": total, "page": page, "page_size": page_size}

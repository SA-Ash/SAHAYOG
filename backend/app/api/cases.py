import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import current_user, require_role
from app.core.errors import AppError
from app.core.security import check_csrf
from app.models.entities import CaseAttachment, CaseStatus, Chain, User
from app.schemas.cases import CaseCreate, CaseOut, CasePage, TransactionInput
from app.services.cases import CaseService
from app.services.extraction import MAX_BYTES, stage_image

router = APIRouter(prefix="/cases", tags=["Cases"])
investigator = require_role("INVESTIGATOR")


@router.post("", response_model=CaseOut, status_code=201, dependencies=[Depends(check_csrf)])
def create_case(
    data: CaseCreate, user: User = Depends(investigator), db: Session = Depends(get_db)
):
    return CaseService.create_case(db, data, user)


@router.get("", response_model=CasePage)
def list_cases(
    status: CaseStatus | None = None,
    chain: Chain | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise AppError("INVALID_DATE_RANGE", "Start date must not follow end date", 422)
    start = datetime.combine(date_from, time.min, timezone.utc) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, timezone.utc) if date_to else None
    audit(user.id, "case.list", {"page": page}, db=db)
    return CaseService.list_cases(db, status, chain, start, end, search, page, page_size)


@router.post("/extract-from-image", dependencies=[Depends(check_csrf)])
def extract(
    file: UploadFile = File(...), user: User = Depends(investigator), db: Session = Depends(get_db)
):
    raw = file.file.read(MAX_BYTES + 1)
    return stage_image(db, user, raw)


@router.get("/attachments/{attachment_id}/image")
def attachment_image(
    attachment_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(CaseAttachment, attachment_id)
    if not row or (row.case_id is None and row.uploaded_by != user.id):
        raise AppError("ATTACHMENT_NOT_FOUND", "Attachment not found", 404)
    path = get_settings().upload_dir / row.file_path
    if not path.is_file():
        raise AppError("ATTACHMENT_NOT_FOUND", "Attachment file not found", 404)
    audit(user.id, "case.attachment_read", {"attachment_id": str(row.id)}, db=db)
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    case = CaseService.get_case(db, case_id)
    audit(user.id, "case.read", {"case_id": str(case_id)}, db=db)
    return case


@router.post(
    "/{case_id}/victim-transactions",
    response_model=CaseOut,
    status_code=201,
    dependencies=[Depends(check_csrf)],
)
def attach(
    case_id: uuid.UUID,
    data: TransactionInput,
    user: User = Depends(investigator),
    db: Session = Depends(get_db),
):
    return CaseService.attach_victim_tx(db, case_id, data, user)

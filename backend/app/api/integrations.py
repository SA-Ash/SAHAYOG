import secrets

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import require_role
from app.core.errors import AppError
from app.core.security import check_csrf
from app.models.entities import User
from app.schemas.cases import CaseCreate, CaseOut, Complaint
from app.services.cases import CaseService
from app.services.sahyog import SahyogClient, get_sahyog_client

router = APIRouter(prefix="/integrations/sahyog", tags=["SAHYOG Integration"])


def service_token(authorization: str = Header(default="")):
    expected = "Bearer " + get_settings().sahyog_service_token
    if not secrets.compare_digest(authorization, expected):
        raise AppError("INVALID_SERVICE_TOKEN", "Service authentication required", 401)


@router.post("/webhook", response_model=CaseOut, dependencies=[Depends(service_token)])
def webhook(data: Complaint, db: Session = Depends(get_db)):
    actor = db.scalar(
        select(User).where(User.email == "sahyog-service@internal", User.is_active.is_(True))
    )
    if not actor:
        raise AppError("SERVICE_ACCOUNT_MISSING", "Provision the SAHYOG service account", 503)
    source = "simulated" if get_settings().sahyog_mode == "mock" else "live"
    if source == "live" and data.source != "live":
        raise AppError(
            "SOURCE_MISMATCH", "Live integration cannot accept simulated complaints", 422
        )
    return CaseService.create_case(
        db,
        CaseCreate(title=data.title, sahyog_ref=data.ref, transaction=data.transaction),
        actor,
        source=source,
        idempotent=True,
    )


@router.post(
    "/complaints/{ref:path}/import", response_model=CaseOut, dependencies=[Depends(check_csrf)]
)
async def import_complaint(
    ref: str,
    user: User = Depends(require_role("INVESTIGATOR")),
    db: Session = Depends(get_db),
    client: SahyogClient = Depends(get_sahyog_client),
):
    complaint = await client.get_complaint(ref)
    return CaseService.create_case(
        db,
        CaseCreate(
            title=complaint.title, sahyog_ref=complaint.ref, transaction=complaint.transaction
        ),
        user,
        source="simulated" if get_settings().sahyog_mode == "mock" else "live",
    )

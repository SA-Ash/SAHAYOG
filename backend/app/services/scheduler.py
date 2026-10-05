import asyncio

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.audit import AuditService
from app.core.db import SessionLocal
from app.models.entities import User, now
from app.models.intelligence import Setting
from app.models.workflow import FreezeRequest, Notification
from app.services.requests import RequestService


async def cycle():
    with SessionLocal() as db:
        expired = await RequestService.expire(db)
        for row in db.scalars(
            select(FreezeRequest).where(
                FreezeRequest.state.in_(["SENT", "ACKNOWLEDGED", "EXPIRED"])
            )
        ):
            try:
                if row.state == "EXPIRED" and row.dispatch_error:
                    await RequestService.dispatch(db, row.id, "release")
                elif row.state in {"SENT", "ACKNOWLEDGED"} and row.sahyog_request_id:
                    await RequestService.poll(db, row)
            except Exception:
                db.rollback()
        check = db.get(Setting, "audit_last_check")
        if not check or check.value.get("date") != now().date().isoformat():
            result = AuditService.verify(db)
            if not check:
                check = Setting(key="audit_last_check", value={})
                db.add(check)
            if not result["valid"]:
                for user in db.scalars(
                    select(User).where(User.role == "ADMIN", User.is_active.is_(True))
                ):
                    db.add(
                        Notification(
                            user_id=user.id, kind="audit_integrity_failed", payload_json=result
                        )
                    )
            check.value = {"date": now().date().isoformat(), **result}
            db.commit()
        from app.models.entities import Case
        from app.models.monitoring import MonitorSettings
        from app.services.monitoring import MonitorService

        for config in db.scalars(select(MonitorSettings)).all():
            case = db.get(Case, config.case_id)
            if case.status != "CLOSED" and (
                not config.last_polled
                or (now() - config.last_polled).total_seconds() >= config.poll_seconds
            ):
                try:
                    await MonitorService.poll(db, case.id, db.get(User, case.created_by))
                except Exception:
                    db.rollback()
        return {"expired": expired}


async def local_scheduler():
    while True:
        try:
            await cycle()
        except SQLAlchemyError:
            pass
        await asyncio.sleep(5)

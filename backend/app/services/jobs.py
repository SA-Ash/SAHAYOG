import asyncio
import uuid

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models.entities import Case, now
from app.models.intelligence import FederatedQuery, Job, TraceRun
from app.schemas.intelligence import TraceParams

active_tasks = set()


async def execute_job(job_id):
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(str(job_id)))
        if not job or job.status not in {"QUEUED", "RUNNING"}:
            return
        changed = db.execute(
            update(Job).where(Job.id == job.id, Job.status == "QUEUED").values(status="RUNNING")
        ).rowcount
        db.commit()
        if not changed:
            return
        try:
            if job.kind == "trace":
                from app.services.traces import TraceService

                run = db.get(TraceRun, uuid.UUID(job.payload_json["trace_run_id"]))
                result = await TraceService.run(
                    db, run, TraceParams.model_validate(run.params_json)
                )
                if job.payload_json.get("refresh_taint"):
                    from app.models.entities import User
                    from app.schemas.workflow import TaintInput
                    from app.services.taint import TaintService

                    await TaintService.run(
                        db,
                        job.case_id,
                        TaintInput(),
                        db.get(User, db.get(Case, job.case_id).created_by),
                    )
            elif job.kind == "federated":
                from app.services.federated import FederatedLookupService

                query = db.get(FederatedQuery, uuid.UUID(job.payload_json["query_id"]))
                result = await FederatedLookupService.run(db, query)
            elif job.kind == "request":
                from app.services.requests import RequestService

                result = await RequestService.dispatch(
                    db,
                    uuid.UUID(job.payload_json["request_id"]),
                    job.payload_json.get("operation", "send"),
                )
            else:
                raise AppError("UNKNOWN_JOB", "Unknown job type", 422)
            job.status, job.progress, job.result_json = "COMPLETED", 100, result
            job.error_json = None
        except Exception as exc:
            db.rollback()
            job = db.get(Job, uuid.UUID(str(job_id)))
            job.status = "FAILED"
            job.error_json = (
                {"code": exc.code, "message": exc.message, "details": exc.details}
                if isinstance(exc, AppError)
                else {
                    "code": "JOB_FAILED",
                    "message": "Job failed; retry or review provider configuration",
                    "details": None,
                }
            )
            if job.kind == "request":
                from app.models.workflow import FreezeRequest

                request = db.get(FreezeRequest, uuid.UUID(job.payload_json["request_id"]))
                request.dispatch_error = {
                    **job.error_json,
                    "retryable": True,
                    "operation": job.payload_json.get("operation", "send"),
                }
            if job.kind == "trace":
                run = db.get(TraceRun, uuid.UUID(job.payload_json["trace_run_id"]))
                run.status, run.finished_at = "FAILED", now()
                case = db.get(Case, job.case_id)
                if case.status == "TRACING":
                    case.status = "OPEN"
        job.finished_at = now()
        db.commit()
        from app.services.traces import emit

        emit(
            db,
            job.case_id,
            "job_done",
            {"job_id": str(job.id), "status": job.status, "error": job.error_json},
        )


def dispatch(job_id):
    if get_settings().job_backend == "celery":
        try:
            from app.workers.tasks import run_job

            run_job.apply_async(args=[str(job_id)], task_id=str(job_id))
        except Exception as exc:
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                job.status = "FAILED"
                job.error_json = {
                    "code": "QUEUE_UNAVAILABLE",
                    "message": "Queue unavailable; retry when Redis recovers",
                }
                if job.kind == "trace":
                    run = db.get(TraceRun, uuid.UUID(job.payload_json["trace_run_id"]))
                    run.status = "FAILED"
                    db.get(Case, job.case_id).status = "OPEN"
                db.commit()
            raise AppError(
                "QUEUE_UNAVAILABLE", "Queue unavailable; retry when Redis recovers", 503
            ) from exc
    else:
        task = asyncio.create_task(execute_job(job_id))
        active_tasks.add(task)
        task.add_done_callback(active_tasks.discard)


async def recover_jobs():
    if get_settings().job_backend != "local":
        return
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with SessionLocal() as db:
            queued = list(db.scalars(select(Job.id).where(Job.status.in_(["QUEUED", "RUNNING"]))))
            db.execute(update(Job).where(Job.status == "RUNNING").values(status="QUEUED"))
            db.commit()
        for job_id in queued:
            dispatch(job_id)
    except SQLAlchemyError:
        return


def job_json(job):
    return {
        "id": str(job.id),
        "case_id": str(job.case_id),
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "result": job.result_json,
        "error": job.error_json,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }

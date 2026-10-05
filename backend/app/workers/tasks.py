import asyncio

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery = Celery("sahyog", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


@celery.task(name="sahyog.run_job", bind=True)
def run_job(self, job_id):
    if self.request.delivery_info.get("redelivered"):
        import uuid

        from app.core.db import SessionLocal
        from app.models.intelligence import Job

        with SessionLocal() as db:
            job = db.get(Job, uuid.UUID(job_id))
            if job and job.status == "RUNNING":
                job.status = "QUEUED"
                db.commit()
    from app.services.jobs import execute_job

    asyncio.run(execute_job(job_id))


@celery.task(name="sahyog.expiry_cycle")
def expiry_cycle():
    from app.services.scheduler import cycle

    return asyncio.run(cycle())


celery.conf.beat_schedule = {
    "request-expiry-and-status": {"task": "sahyog.expiry_cycle", "schedule": 30.0}
}

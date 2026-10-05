from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, cases, integrations, intelligence, monitoring, workflow
from app.core.config import get_settings
from app.core.errors import install_error_handlers


@asynccontextmanager
async def lifespan(_):
    from app.services.jobs import active_tasks, recover_jobs

    await recover_jobs()
    import asyncio

    from app.services.scheduler import local_scheduler

    timer = (
        asyncio.create_task(local_scheduler()) if get_settings().job_backend == "local" else None
    )
    yield
    if timer:
        timer.cancel()
    for task in list(active_tasks):
        task.cancel()


settings = get_settings()
app = FastAPI(
    title="SAHYOG Blockchain Intelligence",
    version="0.16.0",
    description="Complaint intake, replayable ingestion, tracing, attribution and VASP federation.",
    lifespan=lifespan,
)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and response.status_code < 400
        and getattr(request.state, "audit_actor", None)
    ):
        from app.core.audit import audit

        audit(
            request.state.audit_actor,
            "endpoint.mutated",
            {"path": request.url.path, "method": request.method},
            db=request.state.audit_db,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


for router in (
    auth.router,
    cases.router,
    integrations.router,
    intelligence.router,
    workflow.router,
    monitoring.router,
):
    app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    from sqlalchemy import text

    from app.core.db import engine
    from app.core.errors import AppError

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError("DATABASE_UNAVAILABLE", "Database unavailable", 503) from exc
    return {
        "status": "ok",
        "version": "0.16.0",
        "implemented_tasks": list(range(1, 16)),
        "partial_tasks": [16],
        "sahyog_mode": settings.sahyog_mode,
        "audit": "hash-chained",
    }

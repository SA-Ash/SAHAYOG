from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, cases, integrations
from app.core.config import get_settings
from app.core.errors import install_error_handlers

settings = get_settings()
app = FastAPI(title="SAHYOG Blockchain Intelligence", version="0.1.0", description="Task 1: secure complaint intake. Tracing, attribution and approvals are not implemented yet.")
install_error_handlers(app)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"])


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


for router in (auth.router, cases.router, integrations.router):
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
    return {"status": "ok", "version": "0.1.0", "implemented_tasks": [1], "sahyog_mode": settings.sahyog_mode, "audit": "stub"}

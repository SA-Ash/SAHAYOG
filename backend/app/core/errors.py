from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details


def install_error_handlers(app: FastAPI):
    @app.exception_handler(AppError)
    async def domain_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "Check the supplied fields", "details": errors})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"code": "HTTP_ERROR", "message": str(exc.detail), "details": None}, headers=exc.headers)

    @app.exception_handler(IntegrityError)
    async def conflict(_: Request, exc: IntegrityError):
        return JSONResponse(status_code=409, content={"code": "CONFLICT", "message": "This reference or transaction already exists", "details": None})

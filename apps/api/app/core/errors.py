"""Formato de error único para toda la API.

{"error": {"code": str, "message": str, "details": Any | null, "correlation_id": str | null}}
"""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.correlation import get_correlation_id

log = structlog.get_logger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Error de dominio con código estable para el cliente."""

    def __init__(
        self, code: str, message: str, status_code: int = 400, details: Any | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _response(
    status_code: int, code: str, message: str, details: Any | None = None
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, details=details, correlation_id=get_correlation_id()
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _status_code_name(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).name.lower()
    except ValueError:
        return "http_error"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _response(exc.status_code, _status_code_name(exc.status_code), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return _response(422, "validation_error", "Datos de entrada no válidos", details)


class UnhandledErrorMiddleware:
    """Convierte excepciones no controladas en el formato de error común.

    Debe quedar por dentro de CorrelationIdMiddleware: el manejador de `Exception` de
    Starlette se ejecuta en la capa más externa, donde el correlation_id ya no existe.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            log.exception("unhandled_error", exc_info=exc)
            if response_started:
                raise
            response = _response(500, "internal_error", "Error interno")
            await response(scope, receive, send)

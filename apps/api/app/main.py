from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import health
from app.core.config import Environment, get_settings
from app.core.correlation import HEADER as CORRELATION_HEADER
from app.core.correlation import CorrelationIdMiddleware
from app.core.errors import ErrorResponse, UnhandledErrorMiddleware, register_error_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment is not Environment.DEVELOPMENT)

    app = FastAPI(
        title="Metting Agent API",
        version="0.1.0",
        root_path=settings.root_path,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        responses={
            422: {"model": ErrorResponse, "description": "Error de validación"},
            500: {"model": ErrorResponse, "description": "Error interno"},
        },
    )

    # El último middleware añadido es el más externo.
    app.add_middleware(UnhandledErrorMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[CORRELATION_HEADER],
        )
    app.add_middleware(CorrelationIdMiddleware)

    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()

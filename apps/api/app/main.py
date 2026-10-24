from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import health
from app.ai import router as ai_router
from app.auth import oidc_router
from app.auth import router as auth_router
from app.core.config import Environment, get_settings
from app.core.correlation import HEADER as CORRELATION_HEADER
from app.core.correlation import CorrelationIdMiddleware
from app.core.errors import ErrorResponse, UnhandledErrorMiddleware, register_error_handlers
from app.core.logging import configure_logging
from app.core.metrics import MetricsMiddleware
from app.core.root_path import RootPathMiddleware
from app.exports import router as exports_router
from app.orgs import router as orgs_router
from app.recordings import audio as recordings_audio
from app.recordings import router as recordings_router


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment is not Environment.DEVELOPMENT)

    app = FastAPI(
        title="Metting Agent API",
        version="0.1.0",
        # En producción no se publica el inventario de la API (ni la interfaz ni el esquema).
        docs_url=None if settings.is_production else "/docs",
        openapi_url=None if settings.is_production else "/openapi.json",
        redoc_url=None,
        # Sin redirecciones de barra final: Starlette las construye con el esquema y el host
        # que ve detrás del proxy (http, sin /api). Con barra final, la ruta no existe (404).
        redirect_slashes=False,
        responses={
            422: {"model": ErrorResponse, "description": "Error de validación"},
            500: {"model": ErrorResponse, "description": "Error interno"},
        },
    )

    # El último middleware añadido es el más externo.
    app.add_middleware(UnhandledErrorMiddleware)
    if settings.root_path:
        app.add_middleware(RootPathMiddleware, root_path=settings.root_path)
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
    app.add_middleware(MetricsMiddleware)

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth_router.router)
    app.include_router(oidc_router.router)
    app.include_router(orgs_router.router)
    app.include_router(recordings_router.router)
    app.include_router(recordings_audio.router)
    app.include_router(ai_router.router)
    app.include_router(exports_router.router)
    return app


app = create_app()

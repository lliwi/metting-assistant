from collections.abc import Callable

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from redis import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db import get_engine
from app.storage import get_storage

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


def check_database() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def check_redis() -> None:
    client = Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.ping()
    finally:
        client.close()


def check_storage() -> None:
    get_storage().check()


READINESS_CHECKS: dict[str, Callable[[], None]] = {
    "database": check_database,
    "redis": check_redis,
    "storage": check_storage,
}


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Métricas Prometheus (solo red interna: nginx no publica esta ruta)."""
    from app.core.metrics import render_metrics

    body, content_type = render_metrics()
    return Response(body, media_type=content_type)


@router.get("/healthz")
def liveness() -> dict[str, str]:
    """El proceso responde. No comprueba dependencias."""
    return {"status": "ok"}


@router.get("/readyz")
def readiness() -> JSONResponse:
    """El servicio puede atender tráfico: dependencias accesibles."""
    results: dict[str, str] = {}
    for name, check in READINESS_CHECKS.items():
        try:
            check()
            results[name] = "ok"
        except Exception as exc:
            log.warning("readiness_check_failed", check=name, error=str(exc))
            results[name] = "error"
    ready = all(v == "ok" for v in results.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "unavailable", "checks": results},
    )

"""Métricas Prometheus: HTTP (por ruta) y estado del sistema calculado en cada consulta.

Con varios procesos (gunicorn) se usa el modo multiproceso de prometheus_client:
PROMETHEUS_MULTIPROC_DIR apunta a un directorio en /tmp (tmpfs, se vacía en cada arranque).
El endpoint /metrics no se publica: nginx lo bloquea y Prometheus lo consulta en la red interna.
"""

import os
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import structlog
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from redis import Redis
from sqlalchemy import func, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.sessions import now
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import AiOutput, AiOutputStatus, Recording, RecordingStatus, UsageRecord

log = structlog.get_logger(__name__)

_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if _multiproc_dir:
    os.makedirs(_multiproc_dir, exist_ok=True)

HTTP_REQUESTS = Counter(
    "http_requests_total", "Peticiones HTTP atendidas", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Duración de las peticiones HTTP",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

# Colas de Celery (listas de Redis) que se vigilan.
QUEUES = ("default", "media", "asr", "llm", "exports")
# Un trabajo "processing" más antiguo que esto se considera atascado.
STUCK_AFTER = timedelta(hours=1)


class MetricsMiddleware:
    """Cuenta y mide cada petición por plantilla de ruta (/v1/recordings/{recording_id})."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status)).inc()
            HTTP_LATENCY.labels(method, route).observe(time.perf_counter() - started)


class SystemCollector(Collector):
    """Estado del sistema en el momento de la consulta (base de datos, Redis)."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        yield from self._dependencies_and_queues()
        try:
            yield from self._database()
        except Exception as exc:  # la caída de la BD ya se refleja en metting_dependency_up
            log.warning("metrics_database_failed", error=str(exc))

    def _dependencies_and_queues(self) -> Iterator[GaugeMetricFamily]:
        from app.health import READINESS_CHECKS

        up = GaugeMetricFamily("metting_dependency_up", "Dependencia accesible", labels=["name"])
        for name, check in READINESS_CHECKS.items():
            try:
                check()
                up.add_metric([name], 1)
            except Exception:
                up.add_metric([name], 0)
        yield up

        queues = GaugeMetricFamily(
            "metting_queue_length", "Trabajos esperando en la cola", labels=["queue"]
        )
        redis = Redis.from_url(get_settings().redis_url, socket_timeout=2)
        try:
            for queue in QUEUES:
                queues.add_metric([queue], int(redis.llen(queue)))
        except Exception as exc:
            log.warning("metrics_redis_failed", error=str(exc))
        finally:
            redis.close()
        yield queues

    def _database(self) -> Iterator[GaugeMetricFamily]:
        current = now()
        with get_sessionmaker()() as db:
            by_status = GaugeMetricFamily(
                "metting_recordings", "Grabaciones por estado", labels=["status"]
            )
            counts: dict[Any, int] = dict(
                db.execute(select(Recording.status, func.count()).group_by(Recording.status)).all()
            )
            for status in RecordingStatus:
                by_status.add_metric([status.value], counts.get(status, 0))
            yield by_status

            stuck = db.scalar(
                select(func.count()).where(
                    Recording.status == RecordingStatus.PROCESSING,
                    Recording.updated_at < current - STUCK_AFTER,
                )
            )
            yield GaugeMetricFamily(
                "metting_recordings_stuck",
                "Grabaciones procesándose desde hace más de una hora",
                value=stuck or 0,
            )
            failed = db.scalar(
                select(func.count()).where(
                    Recording.status == RecordingStatus.FAILED,
                    Recording.updated_at > current - timedelta(hours=24),
                )
            )
            yield GaugeMetricFamily(
                "metting_recordings_failed_24h", "Grabaciones fallidas en 24 h", value=failed or 0
            )

            ai = GaugeMetricFamily(
                "metting_ai_outputs",
                "Documentos de IA pendientes o fallidos (24 h)",
                labels=["status"],
            )
            pending = db.scalar(
                select(func.count()).where(AiOutput.status == AiOutputStatus.PENDING)
            )
            ai_failed = db.scalar(
                select(func.count()).where(
                    AiOutput.status == AiOutputStatus.FAILED,
                    AiOutput.created_at > current - timedelta(hours=24),
                )
            )
            ai.add_metric(["pending"], pending or 0)
            ai.add_metric(["failed_24h"], ai_failed or 0)
            yield ai

            month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            cost = db.scalar(
                select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
                    UsageRecord.created_at >= month_start
                )
            )
            yield GaugeMetricFamily(
                "metting_ai_cost_month_usd",
                "Coste estimado de IA del mes en curso (todas las organizaciones)",
                value=float(cost or 0),
            )


def render_metrics() -> tuple[bytes, str]:
    registry = CollectorRegistry()
    if _multiproc_dir:
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    else:
        registry.register(HTTP_REQUESTS)
        registry.register(HTTP_LATENCY)
    registry.register(SystemCollector())
    return generate_latest(registry), CONTENT_TYPE_LATEST

"""Notificaciones de cambios de una grabación vía Redis pub/sub, consumidas por SSE."""

import json
from functools import lru_cache

import structlog
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.core.config import get_settings
from app.core.rate_limit import get_redis

log = structlog.get_logger(__name__)


def channel(recording_id: object) -> str:
    return f"recording:{recording_id}"


def publish(redis: Redis, recording_id: object, payload: dict[str, object]) -> None:
    """Best effort: si Redis falla, el cliente obtiene el estado al consultar la grabación."""
    try:
        redis.publish(channel(recording_id), json.dumps(payload, default=str))
    except Exception as exc:
        log.warning("recording_event_publish_failed", error=str(exc))


def get_event_redis() -> Redis:
    return get_redis()


@lru_cache
def get_async_redis() -> AsyncRedis:
    return AsyncRedis.from_url(get_settings().redis_url)

from functools import lru_cache

from redis import Redis

from app.core.config import get_settings
from app.core.errors import AppError


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


class RateLimiter:
    """Ventana fija sobre Redis. Suficiente para frenar fuerza bruta; no es un limitador exacto."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Cuenta un intento y lanza 429 si se supera el límite."""
        full_key = f"rl:{key}"
        pipe = self.redis.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, window_seconds, nx=True)
        count, _ = pipe.execute()
        if int(count) > limit:
            ttl = self.redis.ttl(full_key)
            raise AppError(
                "rate_limited",
                "Demasiados intentos. Inténtalo más tarde.",
                status_code=429,
                details={"retry_after_seconds": max(int(ttl), 1)},
            )

    def reset(self, key: str) -> None:
        self.redis.delete(f"rl:{key}")


def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_redis())

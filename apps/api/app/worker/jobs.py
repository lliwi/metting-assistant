"""Encolado fiable de trabajos largos.

- Marca "en cola" (`queued:*`): el barrido no reencola lo que ya espera en la cola.
- Cerrojo "en curso" (`lock:*`): dos ejecuciones no procesan lo mismo a la vez. Caduca solo:
  si el worker muere, el barrido lo detecta y lo reencola.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from redis import Redis
from redis.exceptions import LockError

log = structlog.get_logger(__name__)

QUEUED_TTL = 2 * 3600
# Una transcripción de horas con un proveedor externo lento cabe en este margen.
PROCESSING_LOCK_TTL = 3 * 3600
AI_LOCK_TTL = 30 * 60


def queued_key(kind: str, job_id: uuid.UUID | str) -> str:
    return f"queued:{kind}:{job_id}"


def lock_key(kind: str, job_id: uuid.UUID | str) -> str:
    return f"lock:{kind}:{job_id}"


def mark_queued(redis: Redis, kind: str, job_id: uuid.UUID | str) -> None:
    redis.set(queued_key(kind, job_id), "1", ex=QUEUED_TTL)


def is_busy(redis: Redis, kind: str, job_id: uuid.UUID | str) -> bool:
    """En cola o en ejecución: el barrido no debe tocarlo."""
    return bool(redis.exists(queued_key(kind, job_id), lock_key(kind, job_id)))


@contextmanager
def exclusive(redis: Redis, kind: str, job_id: uuid.UUID | str, ttl: int) -> Iterator[bool]:
    """Cede True si este proceso tiene el trabajo en exclusiva; False si ya lo procesa otro."""
    redis.delete(queued_key(kind, job_id))
    lock = redis.lock(lock_key(kind, job_id), timeout=ttl)
    if not lock.acquire(blocking=False):
        log.info("job_already_running", kind=kind, job_id=str(job_id))
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock.release()
        except LockError:
            pass  # caducó mientras se ejecutaba: ya no es nuestro

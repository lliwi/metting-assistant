"""Encolado fiable de trabajos largos.

- Marca "en cola" (`queued:*`): el barrido no reencola lo que ya espera en la cola.
- Cerrojo "en curso" (`lock:*`): dos ejecuciones no procesan lo mismo a la vez. Dura poco y
  se renueva mientras el trabajo sigue vivo: si el worker muere (redespliegue, falta de memoria),
  caduca en minutos y el barrido lo reencola, en vez de dejarlo parado horas.
- Reencolados (`requeues:*`): el barrido deja de insistir con un trabajo que nunca termina.
"""

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from redis import Redis
from redis.exceptions import LockError

log = structlog.get_logger(__name__)

QUEUED_TTL = 2 * 3600
# Vida del cerrojo sin renovar: lo que tarda en liberarse si el worker muere.
LOCK_TTL = 120
REQUEUES_TTL = 24 * 3600


def queued_key(kind: str, job_id: uuid.UUID | str) -> str:
    return f"queued:{kind}:{job_id}"


def lock_key(kind: str, job_id: uuid.UUID | str) -> str:
    return f"lock:{kind}:{job_id}"


def mark_queued(redis: Redis, kind: str, job_id: uuid.UUID | str) -> None:
    redis.set(queued_key(kind, job_id), "1", ex=QUEUED_TTL)


def requeues_key(kind: str, job_id: uuid.UUID | str) -> str:
    return f"requeues:{kind}:{job_id}"


def count_requeue(redis: Redis, kind: str, job_id: uuid.UUID | str) -> int:
    """Suma un reencolado del barrido y devuelve cuántos lleva (en las últimas 24 h)."""
    key = requeues_key(kind, job_id)
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, REQUEUES_TTL, nx=True)
    count, _ = pipe.execute()
    return int(count)


def is_busy(redis: Redis, kind: str, job_id: uuid.UUID | str) -> bool:
    """En cola o en ejecución: el barrido no debe tocarlo."""
    return bool(redis.exists(queued_key(kind, job_id), lock_key(kind, job_id)))


@contextmanager
def exclusive(
    redis: Redis, kind: str, job_id: uuid.UUID | str, ttl: int = LOCK_TTL
) -> Iterator[bool]:
    """Cede True si este proceso tiene el trabajo en exclusiva; False si ya lo procesa otro.

    Mientras dura, un hilo renueva el cerrojo cada tercio de `ttl`.
    """
    redis.delete(queued_key(kind, job_id))
    # thread_local=False: el hilo de renovación necesita el token del cerrojo.
    lock = redis.lock(lock_key(kind, job_id), timeout=ttl, thread_local=False)
    if not lock.acquire(blocking=False):
        log.info("job_already_running", kind=kind, job_id=str(job_id))
        yield False
        return
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(ttl / 3):
            try:
                lock.reacquire()
            except LockError:
                log.warning("job_lock_lost", kind=kind, job_id=str(job_id))
                return
            except Exception:  # Redis caído un momento: se reintenta en la siguiente vuelta.
                log.warning("job_lock_renew_failed", kind=kind, job_id=str(job_id), exc_info=True)

    renewer = threading.Thread(target=renew, name=f"lock-{kind}", daemon=True)
    renewer.start()
    try:
        yield True
    finally:
        stop.set()
        renewer.join(timeout=5)
        try:
            lock.release()
        except LockError:
            pass  # caducó mientras se ejecutaba: ya no es nuestro

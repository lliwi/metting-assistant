import uuid
from typing import Any

import structlog

from app.core.rate_limit import get_redis
from app.db import get_sessionmaker
from app.media.antivirus import ScannerUnavailable, get_scanner
from app.media.ffmpeg import MediaError
from app.media.imports import mark_failed, prepare_import
from app.notifications.email import deliver_via_smtp
from app.storage import get_storage
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="app.worker.tasks.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    """Tarea mínima para comprobar que el worker consume de la cola."""
    return "pong"


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.send_email",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def send_email(to: str, subject: str, body: str) -> None:
    deliver_via_smtp(to, subject, body)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.media.prepare_import", bind=True, max_retries=10
)
def prepare_import_task(self: Any, recording_id: str) -> None:
    rid = uuid.UUID(recording_id)
    with get_sessionmaker()() as db:
        try:
            prepare_import(db, get_storage(), get_scanner(), get_redis(), rid)
        except ScannerUnavailable as exc:
            # Sin antivirus no se acepta el archivo: se reintenta y, al final, se rechaza.
            if self.request.retries >= self.max_retries:
                db.rollback()
                mark_failed(db, get_redis(), rid, "scan_unavailable")
                return
            log.warning("antivirus_unavailable", recording_id=recording_id, error=str(exc))
            raise self.retry(exc=exc, countdown=min(600, 30 * 2**self.request.retries)) from exc
        except MediaError as exc:
            db.rollback()
            mark_failed(db, get_redis(), rid, exc.code)

import uuid
from typing import Any, cast

import structlog
from sqlalchemy import select

from app.ai.asr import AsrError, get_asr_provider
from app.ai.diarization import get_diarizer
from app.ai.llm import LlmError, get_llm_provider
from app.ai.service import mark_output_failed, outputs_after_transcription, run_output
from app.core.config import TranscriptionEngine
from app.core.rate_limit import get_redis
from app.db import get_sessionmaker
from app.media.antivirus import ScannerUnavailable, get_scanner
from app.media.ffmpeg import MediaError
from app.media.imports import mark_failed
from app.media.pipeline import process_recording
from app.models import Recording
from app.notifications.email import deliver_via_smtp
from app.storage import get_storage
from app.worker.celery_app import celery_app
from app.worker.jobs import AI_LOCK_TTL, PROCESSING_LOCK_TTL, exclusive, mark_queued
from app.worker.sweeper import sweep

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
    name="app.worker.tasks.asr.process_recording", bind=True, max_retries=10
)
def process_recording_task(self: Any, recording_id: str) -> None:
    """Prepara el audio (importación o captura del navegador) y lo transcribe."""
    rid = uuid.UUID(recording_id)
    with (
        exclusive(get_redis(), "recording", rid, PROCESSING_LOCK_TTL) as owned,
        get_sessionmaker()() as db,
    ):
        if not owned:
            return
        try:
            engine = cast(
                TranscriptionEngine | None,
                db.scalar(select(Recording.transcription_engine).where(Recording.id == rid)),
            )
            process_recording(
                db,
                get_storage(),
                get_scanner(),
                get_asr_provider(engine),
                get_redis(),
                rid,
                diarizer=get_diarizer(engine),
            )
            for output_id in outputs_after_transcription(db, rid):
                enqueue_ai_output(output_id)
        except (ScannerUnavailable, AsrError) as exc:
            transient = isinstance(exc, ScannerUnavailable) or exc.transient
            db.rollback()
            if transient and self.request.retries < self.max_retries:
                log.warning("processing_retry", recording_id=recording_id, error=str(exc))
                # Mientras espera el reintento, el barrido no debe reencolarlo.
                mark_queued(get_redis(), "recording", rid)
                raise self.retry(exc=exc, countdown=min(600, 30 * 2**self.request.retries)) from exc
            log.error("processing_failed", recording_id=recording_id, error=str(exc))
            # Sin antivirus no se acepta el archivo; un ASR que falla siempre no se reintenta.
            code = (
                "scan_unavailable"
                if isinstance(exc, ScannerUnavailable)
                else "transcription_failed"
            )
            mark_failed(db, get_redis(), rid, code)
        except MediaError as exc:
            log.error("processing_failed", recording_id=recording_id, error=str(exc))
            db.rollback()
            mark_failed(db, get_redis(), rid, exc.code)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.llm.generate_ai_output", bind=True, max_retries=6
)
def generate_ai_output_task(self: Any, output_id: str) -> None:
    """Resumen o acta con el LLM configurado (Ollama u OpenAI)."""
    oid = uuid.UUID(output_id)
    with (
        exclusive(get_redis(), "ai_output", oid, AI_LOCK_TTL) as owned,
        get_sessionmaker()() as db,
    ):
        if not owned:
            return
        try:
            run_output(db, get_llm_provider(), get_redis(), oid)
        except LlmError as exc:
            db.rollback()
            if exc.transient and self.request.retries < self.max_retries:
                log.warning("ai_output_retry", output_id=output_id, error=str(exc))
                mark_queued(get_redis(), "ai_output", oid)
                raise self.retry(exc=exc, countdown=min(600, 20 * 2**self.request.retries)) from exc
            log.error("ai_output_failed", output_id=output_id, error=str(exc))
            mark_output_failed(db, get_redis(), oid, "generation_failed")


def enqueue_recording(recording_id: uuid.UUID) -> None:
    mark_queued(get_redis(), "recording", recording_id)
    process_recording_task.delay(str(recording_id))


def enqueue_ai_output(output_id: uuid.UUID) -> None:
    mark_queued(get_redis(), "ai_output", output_id)
    generate_ai_output_task.delay(str(output_id))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.purge_storage_prefix",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=8,
)
def purge_storage_prefix(prefix: str) -> int:
    """Borra los objetos de una organización eliminada (la base de datos ya no los referencia)."""
    # Solo el prefijo exacto de una organización: nunca "orgs/" ni uno parcial ("orgs/ab"
    # también casaría con "orgs/abc…").
    parts = prefix.split("/")
    try:
        valid = (
            len(parts) == 3 and parts[0] == "orgs" and parts[2] == "" and bool(uuid.UUID(parts[1]))
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"Prefijo no permitido: {prefix!r}")
    deleted = get_storage().delete_prefix(prefix)
    log.info("storage_prefix_purged", prefix=prefix, objects=deleted)
    return int(deleted)


@celery_app.task(name="app.worker.tasks.sweep_stuck_jobs")  # type: ignore[untyped-decorator]
def sweep_stuck_jobs() -> tuple[int, int]:
    """Lo programa celery beat cada 10 minutos."""
    with get_sessionmaker()() as db:
        return sweep(db, get_redis(), enqueue_recording, enqueue_ai_output)

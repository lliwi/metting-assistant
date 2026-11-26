import uuid
from typing import Any, cast

import structlog
from sqlalchemy import select

from app.ai.asr import AsrError, get_asr_provider
from app.ai.diarization import get_diarizer
from app.ai.document_translation import (
    mark_document_translation_failed,
    run_document_translation,
)
from app.ai.llm import LlmError, get_llm_provider
from app.ai.service import mark_output_failed, outputs_after_transcription, run_output
from app.ai.translation import mark_translation_failed, run_translation
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
from app.worker.jobs import exclusive, mark_queued, requeues_key
from app.worker.retention import apply_retention
from app.worker.sweeper import sweep

log = structlog.get_logger(__name__)

# Errores no previstos (un fallo de programación, un servicio que responde algo inesperado):
# unos pocos reintentos por si era pasajero y, después, fallida con aviso. Sin esto la grabación
# se quedaba "procesando" para siempre y el barrido la reintentaba cada 10 minutos.
UNEXPECTED_RETRIES = 3


def _unexpected_countdown(retries: int) -> int:
    return int(60 * 2**retries)


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
        exclusive(get_redis(), "recording", rid) as owned,
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
        except Exception as exc:
            db.rollback()
            if self.request.retries < UNEXPECTED_RETRIES:
                log.exception("processing_retry_unexpected", recording_id=recording_id)
                mark_queued(get_redis(), "recording", rid)
                raise self.retry(
                    exc=exc, countdown=_unexpected_countdown(self.request.retries)
                ) from exc
            log.exception("processing_failed_unexpected", recording_id=recording_id)
            mark_failed(db, get_redis(), rid, "processing_failed")
        else:
            get_redis().delete(requeues_key("recording", rid))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.llm.generate_ai_output", bind=True, max_retries=6
)
def generate_ai_output_task(self: Any, output_id: str) -> None:
    """Resumen o acta con el LLM configurado (Ollama u OpenAI)."""
    oid = uuid.UUID(output_id)
    with (
        exclusive(get_redis(), "ai_output", oid) as owned,
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
        except Exception as exc:
            db.rollback()
            if self.request.retries < UNEXPECTED_RETRIES:
                log.exception("ai_output_retry_unexpected", output_id=output_id)
                mark_queued(get_redis(), "ai_output", oid)
                raise self.retry(
                    exc=exc, countdown=_unexpected_countdown(self.request.retries)
                ) from exc
            log.exception("ai_output_failed_unexpected", output_id=output_id)
            mark_output_failed(db, get_redis(), oid, "generation_failed")
        else:
            get_redis().delete(requeues_key("ai_output", oid))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.llm.translate_transcript", bind=True, max_retries=6
)
def translate_transcript_task(self: Any, translation_id: str) -> None:
    """Traduce una transcripción. Lo ya traducido se conserva entre reintentos."""
    tid = uuid.UUID(translation_id)
    with (
        exclusive(get_redis(), "translation", tid) as owned,
        get_sessionmaker()() as db,
    ):
        if not owned:
            return
        try:
            run_translation(db, get_llm_provider(), get_redis(), tid)
        except LlmError as exc:
            db.rollback()
            if exc.transient and self.request.retries < self.max_retries:
                log.warning("translation_retry", translation_id=translation_id, error=str(exc))
                mark_queued(get_redis(), "translation", tid)
                raise self.retry(exc=exc, countdown=min(600, 20 * 2**self.request.retries)) from exc
            log.error("translation_failed", translation_id=translation_id, error=str(exc))
            mark_translation_failed(db, get_redis(), tid, "translation_failed")
        except Exception as exc:
            db.rollback()
            if self.request.retries < UNEXPECTED_RETRIES:
                log.exception("translation_retry_unexpected", translation_id=translation_id)
                mark_queued(get_redis(), "translation", tid)
                raise self.retry(
                    exc=exc, countdown=_unexpected_countdown(self.request.retries)
                ) from exc
            log.exception("translation_failed_unexpected", translation_id=translation_id)
            mark_translation_failed(db, get_redis(), tid, "translation_failed")
        else:
            get_redis().delete(requeues_key("translation", tid))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.llm.translate_document", bind=True, max_retries=6
)
def translate_document_task(self: Any, translation_id: str) -> None:
    """Traduce un documento de IA (resumen, acta, apuntes)."""
    tid = uuid.UUID(translation_id)
    with (
        exclusive(get_redis(), "document_translation", tid) as owned,
        get_sessionmaker()() as db,
    ):
        if not owned:
            return
        try:
            run_document_translation(db, get_llm_provider(), get_redis(), tid)
        except LlmError as exc:
            db.rollback()
            if exc.transient and self.request.retries < self.max_retries:
                log.warning("document_translation_retry", translation_id=translation_id)
                mark_queued(get_redis(), "document_translation", tid)
                raise self.retry(exc=exc, countdown=min(600, 20 * 2**self.request.retries)) from exc
            log.error("document_translation_failed", translation_id=translation_id, error=str(exc))
            mark_document_translation_failed(db, get_redis(), tid, "translation_failed")
        except Exception as exc:
            db.rollback()
            if self.request.retries < UNEXPECTED_RETRIES:
                log.exception(
                    "document_translation_retry_unexpected", translation_id=translation_id
                )
                mark_queued(get_redis(), "document_translation", tid)
                raise self.retry(
                    exc=exc, countdown=_unexpected_countdown(self.request.retries)
                ) from exc
            log.exception("document_translation_failed_unexpected", translation_id=translation_id)
            mark_document_translation_failed(db, get_redis(), tid, "translation_failed")
        else:
            get_redis().delete(requeues_key("document_translation", tid))


def enqueue_recording(recording_id: uuid.UUID) -> None:
    mark_queued(get_redis(), "recording", recording_id)
    process_recording_task.delay(str(recording_id))


def enqueue_ai_output(output_id: uuid.UUID) -> None:
    mark_queued(get_redis(), "ai_output", output_id)
    generate_ai_output_task.delay(str(output_id))


def enqueue_translation(translation_id: uuid.UUID) -> None:
    mark_queued(get_redis(), "translation", translation_id)
    translate_transcript_task.delay(str(translation_id))


def enqueue_document_translation(translation_id: uuid.UUID) -> None:
    mark_queued(get_redis(), "document_translation", translation_id)
    translate_document_task.delay(str(translation_id))


def request_document_translation(translation_id: uuid.UUID) -> None:
    """Petición de una persona: los reencolados de un intento anterior no cuentan."""
    get_redis().delete(requeues_key("document_translation", translation_id))
    enqueue_document_translation(translation_id)


def request_translation(translation_id: uuid.UUID) -> None:
    """Petición de una persona: los reencolados de un intento anterior no cuentan."""
    get_redis().delete(requeues_key("translation", translation_id))
    enqueue_translation(translation_id)


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _purgeable(prefix: str) -> bool:
    """Solo el prefijo exacto de una organización (orgs/<id>/) o de una grabación
    (orgs/<id>/recordings/<id>/): nunca "orgs/" ni uno parcial ("orgs/ab" también casaría con
    "orgs/abc…")."""
    parts = prefix.split("/")
    if len(parts) == 3:
        return parts[0] == "orgs" and _is_uuid(parts[1]) and parts[2] == ""
    if len(parts) == 5:
        return (
            parts[0] == "orgs"
            and _is_uuid(parts[1])
            and parts[2] == "recordings"
            and _is_uuid(parts[3])
            and parts[4] == ""
        )
    return False


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.purge_storage_prefix",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=8,
)
def purge_storage_prefix(prefix: str) -> int:
    """Borra los objetos de una organización o una grabación eliminadas (la base de datos ya
    no los referencia)."""
    if not _purgeable(prefix):
        raise ValueError(f"Prefijo no permitido: {prefix!r}")
    deleted = get_storage().delete_prefix(prefix)
    log.info("storage_prefix_purged", prefix=prefix, objects=deleted)
    return int(deleted)


@celery_app.task(name="app.worker.tasks.sweep_stuck_jobs")  # type: ignore[untyped-decorator]
def sweep_stuck_jobs() -> tuple[int, int, int, int]:
    """Lo programa celery beat cada 10 minutos."""
    with get_sessionmaker()() as db:
        return sweep(
            db,
            get_redis(),
            enqueue_recording,
            enqueue_ai_output,
            enqueue_translation,
            enqueue_document_translation,
        )


@celery_app.task(name="app.worker.tasks.apply_retention")  # type: ignore[untyped-decorator]
def apply_retention_task() -> int:
    """Lo programa celery beat cada noche."""
    with get_sessionmaker()() as db:
        return apply_retention(db, purge_storage_prefix.delay)

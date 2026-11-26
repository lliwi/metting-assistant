"""Barrido periódico: reencola trabajos que se perdieron (worker caído, Redis reiniciado)."""

import uuid
from collections.abc import Callable
from datetime import timedelta

import structlog
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.document_translation import mark_document_translation_failed
from app.ai.service import mark_output_failed
from app.ai.translation import mark_translation_failed
from app.auth.sessions import now
from app.media.imports import mark_failed
from app.models import (
    AiOutput,
    AiOutputStatus,
    AiOutputTranslation,
    Recording,
    RecordingStatus,
    TranscriptTranslation,
)
from app.worker.jobs import count_requeue, is_busy

log = structlog.get_logger(__name__)

# Margen antes de considerar perdido algo que no está ni en cola ni en ejecución.
STALE_AFTER = timedelta(minutes=10)
# Un trabajo que se pierde una y otra vez (el worker muere siempre con él: falta de memoria, un
# audio que cuelga FFmpeg) se da por fallido: el usuario lo ve y puede reprocesarlo.
MAX_REQUEUES = 3


def sweep(
    db: Session,
    redis: Redis,
    enqueue_recording: Callable[[uuid.UUID], None],
    enqueue_output: Callable[[uuid.UUID], None],
    enqueue_translation: Callable[[uuid.UUID], None],
    enqueue_document_translation: Callable[[uuid.UUID], None],
) -> tuple[int, int, int, int]:
    cutoff = now() - STALE_AFTER
    recordings = db.scalars(
        select(Recording.id).where(
            Recording.status.in_([RecordingStatus.PENDING, RecordingStatus.PROCESSING]),
            Recording.updated_at < cutoff,
        )
    ).all()
    outputs = db.scalars(
        select(AiOutput.id).where(
            AiOutput.status == AiOutputStatus.PENDING, AiOutput.updated_at < cutoff
        )
    ).all()
    translations = db.scalars(
        select(TranscriptTranslation.id).where(
            TranscriptTranslation.status == AiOutputStatus.PENDING,
            TranscriptTranslation.updated_at < cutoff,
        )
    ).all()
    document_translations = db.scalars(
        select(AiOutputTranslation.id).where(
            AiOutputTranslation.status == AiOutputStatus.PENDING,
            AiOutputTranslation.updated_at < cutoff,
        )
    ).all()
    db.rollback()

    requeued_recordings = []
    for recording_id in recordings:
        if is_busy(redis, "recording", recording_id):
            continue
        if count_requeue(redis, "recording", recording_id) > MAX_REQUEUES:
            log.error("recording_given_up", recording_id=str(recording_id))
            mark_failed(
                db, redis, recording_id, "processing_stuck", action="recording.processing_stuck"
            )
            continue
        requeued_recordings.append(recording_id)
        enqueue_recording(recording_id)

    requeued_outputs = []
    for output_id in outputs:
        if is_busy(redis, "ai_output", output_id):
            continue
        if count_requeue(redis, "ai_output", output_id) > MAX_REQUEUES:
            log.error("ai_output_given_up", output_id=str(output_id))
            mark_output_failed(db, redis, output_id, "generation_stuck")
            continue
        requeued_outputs.append(output_id)
        enqueue_output(output_id)

    requeued_translations = []
    for translation_id in translations:
        if is_busy(redis, "translation", translation_id):
            continue
        if count_requeue(redis, "translation", translation_id) > MAX_REQUEUES:
            log.error("translation_given_up", translation_id=str(translation_id))
            mark_translation_failed(db, redis, translation_id, "translation_stuck")
            continue
        requeued_translations.append(translation_id)
        enqueue_translation(translation_id)

    requeued_documents = []
    for translation_id in document_translations:
        if is_busy(redis, "document_translation", translation_id):
            continue
        if count_requeue(redis, "document_translation", translation_id) > MAX_REQUEUES:
            log.error("document_translation_given_up", translation_id=str(translation_id))
            mark_document_translation_failed(db, redis, translation_id, "translation_stuck")
            continue
        requeued_documents.append(translation_id)
        enqueue_document_translation(translation_id)

    if requeued_recordings or requeued_outputs or requeued_translations or requeued_documents:
        log.warning(
            "stuck_jobs_requeued",
            recordings=[str(r) for r in requeued_recordings],
            ai_outputs=[str(o) for o in requeued_outputs],
            translations=[str(t) for t in requeued_translations],
            document_translations=[str(t) for t in requeued_documents],
        )
    return (
        len(requeued_recordings),
        len(requeued_outputs),
        len(requeued_translations),
        len(requeued_documents),
    )

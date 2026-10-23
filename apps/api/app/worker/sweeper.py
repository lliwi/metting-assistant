"""Barrido periódico: reencola trabajos que se perdieron (worker caído, Redis reiniciado)."""

import uuid
from collections.abc import Callable
from datetime import timedelta

import structlog
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import now
from app.models import AiOutput, AiOutputStatus, Recording, RecordingStatus
from app.worker.jobs import is_busy

log = structlog.get_logger(__name__)

# Margen antes de considerar perdido algo que no está ni en cola ni en ejecución.
STALE_AFTER = timedelta(minutes=10)


def sweep(
    db: Session,
    redis: Redis,
    enqueue_recording: Callable[[uuid.UUID], None],
    enqueue_output: Callable[[uuid.UUID], None],
) -> tuple[int, int]:
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
    db.rollback()

    requeued_recordings = [r for r in recordings if not is_busy(redis, "recording", r)]
    requeued_outputs = [o for o in outputs if not is_busy(redis, "ai_output", o)]
    for recording_id in requeued_recordings:
        enqueue_recording(recording_id)
    for output_id in requeued_outputs:
        enqueue_output(output_id)
    if requeued_recordings or requeued_outputs:
        log.warning(
            "stuck_jobs_requeued",
            recordings=[str(r) for r in requeued_recordings],
            ai_outputs=[str(o) for o in requeued_outputs],
        )
    return len(requeued_recordings), len(requeued_outputs)

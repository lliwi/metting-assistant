"""Ciclo de vida de los documentos de IA (resumen, acta)."""

import uuid

import structlog
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.documents import PROMPT_VERSION, Line, attach_references, clean_attendees, generate
from app.ai.llm import LlmProvider
from app.ai.usage import record_usage
from app.audit import record_audit_event
from app.auth.sessions import now
from app.core.config import get_settings
from app.models import (
    AiOutput,
    AiOutputKind,
    AiOutputStatus,
    AuditResult,
    Recording,
    RecordingStatus,
    Transcript,
)
from app.recordings import events

log = structlog.get_logger(__name__)


def latest_transcript(db: Session, recording_id: uuid.UUID) -> Transcript | None:
    return db.scalars(
        select(Transcript)
        .where(Transcript.recording_id == recording_id)
        .order_by(Transcript.version.desc())
        .limit(1)
    ).first()


def create_outputs(
    db: Session,
    recording: Recording,
    transcript: Transcript,
    kinds: list[AiOutputKind],
    requested_by: uuid.UUID | None = None,
) -> list[AiOutput]:
    outputs = [
        AiOutput(
            recording_id=recording.id,
            transcript_id=transcript.id,
            kind=kind,
            status=AiOutputStatus.PENDING,
            prompt_version=PROMPT_VERSION,
            content={},
            requested_by_id=requested_by,
        )
        for kind in kinds
    ]
    db.add_all(outputs)
    db.flush()
    return outputs


def outputs_after_transcription(db: Session, recording_id: uuid.UUID) -> list[uuid.UUID]:
    """Tras transcribir, crea el resumen y el acta de la nueva versión (si está activado)."""
    if not get_settings().ai_auto_generate:
        return []
    rec = db.get(Recording, recording_id)
    if rec is None or rec.status is not RecordingStatus.TRANSCRIBED:
        return []
    transcript = latest_transcript(db, rec.id)
    if transcript is None or not transcript.segments:
        return []
    already = db.scalars(select(AiOutput.id).where(AiOutput.transcript_id == transcript.id)).first()
    if already:
        return []
    outputs = create_outputs(db, rec, transcript, [AiOutputKind.SUMMARY, AiOutputKind.MINUTES])
    db.commit()
    return [o.id for o in outputs]


def _publish(redis: Redis, output: AiOutput) -> None:
    events.publish(
        redis,
        output.recording_id,
        {
            "type": "ai_output",
            "id": str(output.id),
            "kind": output.kind.value,
            "status": output.status.value,
        },
    )


def mark_output_failed(db: Session, redis: Redis, output_id: uuid.UUID, code: str) -> None:
    output = db.get(AiOutput, output_id, with_for_update=True)
    if output is None or output.status is not AiOutputStatus.PENDING:
        return
    output.status = AiOutputStatus.FAILED
    output.error_code = code
    output.completed_at = now()
    rec = db.get_one(Recording, output.recording_id)
    record_audit_event(
        db,
        action="ai_output.failed",
        result=AuditResult.FAILURE,
        organization_id=rec.organization_id,
        resource_type="ai_output",
        resource_id=output.id,
        metadata={"kind": output.kind.value, "reason": code},
    )
    db.commit()
    _publish(redis, output)


def run_output(db: Session, llm: LlmProvider, redis: Redis, output_id: uuid.UUID) -> None:
    """Genera un documento pendiente. Idempotente. LlmError transitorio: lo reintenta la tarea."""
    settings = get_settings()
    output = db.get(AiOutput, output_id)
    if output is None or output.status is not AiOutputStatus.PENDING:
        return
    transcript = db.get_one(Transcript, output.transcript_id)
    rec = db.get_one(Recording, output.recording_id)
    lines = [Line(s.idx, s.start_ms, s.speaker, s.text) for s in transcript.segments]
    names = dict(transcript.speaker_names or {})
    language = transcript.language or rec.language
    kind = output.kind.value
    db.rollback()  # sin transacción abierta mientras el modelo trabaja (puede tardar minutos)

    context = settings.ollama_num_ctx if llm.name == "ollama" else 100_000
    usage_start = llm.usage.copy()
    result = generate(llm, kind, lines, names, language, context_tokens=context)
    usage = llm.usage.since(usage_start)

    output = db.get_one(AiOutput, output_id, with_for_update=True)
    if output.status is not AiOutputStatus.PENDING:
        return
    content = clean_attendees(result.content, {line.speaker for line in lines if line.speaker})
    output.content = attach_references(content, {line.idx: line.start_ms for line in lines})
    output.provider = result.provider
    output.model = result.model
    output.status = AiOutputStatus.READY
    output.error_code = None
    output.completed_at = now()
    record_usage(
        db,
        organization_id=rec.organization_id,
        recording_id=rec.id,
        kind=kind,
        provider=result.provider,
        model=result.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    record_audit_event(
        db,
        action="ai_output.generated",
        organization_id=rec.organization_id,
        resource_type="ai_output",
        resource_id=output.id,
        metadata={
            "kind": kind,
            "provider": result.provider,
            "model": result.model,
            "prompt_version": output.prompt_version,
            "chunks": result.chunks,
        },
    )
    db.commit()
    log.info("ai_output_ready", output_id=str(output.id), kind=kind, chunks=result.chunks)
    _publish(redis, output)

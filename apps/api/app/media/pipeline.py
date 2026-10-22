"""Procesamiento de una grabación cerrada: preparar el audio y transcribirlo."""

import tempfile
import uuid
from pathlib import Path

import structlog
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.asr import AsrProvider
from app.audit import record_audit_event
from app.core.config import get_settings
from app.media.antivirus import Scanner
from app.media.ffmpeg import concat_audio, probe
from app.media.imports import audio_key, prepare_import
from app.models import (
    AudioSegment,
    Recording,
    RecordingSource,
    RecordingStatus,
    Transcript,
    TranscriptSegment,
)
from app.recordings import events
from app.storage import ObjectStorage

log = structlog.get_logger(__name__)

ACTIVE = (RecordingStatus.PENDING, RecordingStatus.PROCESSING)


def _set_processing(db: Session, redis: Redis, recording_id: uuid.UUID) -> None:
    rec = db.get_one(Recording, recording_id, with_for_update=True)
    rec.status = RecordingStatus.PROCESSING
    db.commit()
    events.publish(redis, rec.id, {"type": "status", "status": rec.status.value})


def normalize_browser_capture(
    db: Session, storage: ObjectStorage, recording_id: uuid.UUID, workdir: Path
) -> str:
    """Une los segmentos de todas las pistas, en orden, en un único audio."""
    rec = db.get_one(Recording, recording_id)
    segments = list(
        db.scalars(
            select(AudioSegment)
            .where(AudioSegment.recording_id == rec.id)
            .order_by(AudioSegment.track, AudioSegment.seq)
        )
    )
    key = audio_key(rec)
    db.rollback()
    inputs = []
    for i, segment in enumerate(segments):
        path = workdir / f"{i:05d}.{segment.object_key.rsplit('.', 1)[-1]}"
        path.write_bytes(storage.get(segment.object_key))
        inputs.append(path)
    output = workdir / "audio.ogg"
    concat_audio(inputs, output)
    duration_ms = probe(output).duration_ms
    storage.put_file(key, str(output), "audio/ogg")

    rec = db.get_one(Recording, recording_id, with_for_update=True)
    rec.audio_object_key = key
    # La duración medida sustituye a la declarada por el navegador.
    rec.duration_ms = duration_ms or rec.duration_ms
    db.commit()
    return key


def process_recording(
    db: Session,
    storage: ObjectStorage,
    scanner: Scanner,
    asr: AsrProvider,
    redis: Redis,
    recording_id: uuid.UUID,
) -> None:
    """Idempotente: una grabación ya transcrita o fallida no se vuelve a procesar."""
    settings = get_settings()
    rec = db.get(Recording, recording_id)
    if rec is None or rec.status not in ACTIVE:
        return

    if rec.source is RecordingSource.IMPORT and not rec.audio_object_key:
        prepare_import(db, storage, scanner, redis, recording_id)
        db.expire_all()
        rec = db.get_one(Recording, recording_id)
        if rec.status not in ACTIVE or not rec.audio_object_key:
            return  # rechazada en la preparación

    language = rec.language
    _set_processing(db, redis, recording_id)

    with tempfile.TemporaryDirectory(dir=settings.scratch_dir) as tmp:
        workdir = Path(tmp)
        key = rec.audio_object_key or normalize_browser_capture(db, storage, recording_id, workdir)
        audio = workdir / "transcribe.ogg"
        audio.write_bytes(storage.get(key))
        result = asr.transcribe(audio, language=language)

    rec = db.get_one(Recording, recording_id, with_for_update=True)
    if rec.status is not RecordingStatus.PROCESSING:
        return  # cancelada mientras se transcribía
    version = (
        db.scalar(select(func.max(Transcript.version)).where(Transcript.recording_id == rec.id))
        or 0
    ) + 1
    transcript = Transcript(
        recording_id=rec.id,
        version=version,
        language=result.language or language,
        provider=result.provider,
        model=result.model,
        content="\n".join(s.text for s in result.segments),
        processing_ms=result.processing_ms,
    )
    db.add(transcript)
    db.flush()
    db.add_all(
        TranscriptSegment(
            transcript_id=transcript.id,
            idx=i,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            text=s.text,
            confidence=s.confidence,
        )
        for i, s in enumerate(result.segments)
    )
    rec.status = RecordingStatus.TRANSCRIBED
    rec.error_code = None
    record_audit_event(
        db,
        action="recording.transcribed",
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={
            "provider": result.provider,
            "model": result.model,
            "version": version,
            "segments": len(result.segments),
            "audio_ms": rec.duration_ms,
            "processing_ms": result.processing_ms,
        },
    )
    db.commit()
    log.info(
        "recording_transcribed",
        recording_id=str(rec.id),
        provider=result.provider,
        model=result.model,
        audio_ms=rec.duration_ms,
        processing_ms=result.processing_ms,
    )
    events.publish(
        redis, rec.id, {"type": "status", "status": rec.status.value, "transcript_version": version}
    )

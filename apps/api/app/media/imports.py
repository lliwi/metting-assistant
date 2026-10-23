"""Preparación de un archivo importado: unir bloques, antivirus, validar y extraer audio."""

import hashlib
import tempfile
import uuid
from pathlib import Path

import structlog
from redis import Redis
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.core.config import get_settings
from app.media.antivirus import Scanner
from app.media.ffmpeg import MediaError, extract_audio, probe
from app.models import AudioSegment, AuditResult, Recording, RecordingSource, RecordingStatus
from app.recordings import events
from app.storage import ObjectStorage

log = structlog.get_logger(__name__)

REJECTED = "recording.import_rejected"


def audio_key(rec: Recording) -> str:
    return f"orgs/{rec.organization_id}/recordings/{rec.id}/audio/source.m4a"


def mark_failed(
    db: Session,
    redis: Redis,
    recording_id: uuid.UUID,
    code: str,
    *,
    action: str = "recording.processing_failed",
) -> None:
    rec = db.get(Recording, recording_id, with_for_update=True)
    if rec is None or rec.status not in (RecordingStatus.PENDING, RecordingStatus.PROCESSING):
        return
    rec.status = RecordingStatus.FAILED
    rec.error_code = code
    record_audit_event(
        db,
        action=action,
        result=AuditResult.FAILURE,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={"reason": code},
    )
    db.commit()
    events.publish(redis, rec.id, {"type": "status", "status": rec.status.value, "error": code})
    log.info("import_failed", recording_id=str(rec.id), reason=code)


def prepare_import(
    db: Session, storage: ObjectStorage, scanner: Scanner, redis: Redis, recording_id: uuid.UUID
) -> None:
    """Idempotente: si el audio ya está extraído o la grabación no está pendiente, no hace nada.

    No mantiene transacciones abiertas durante el procesamiento (puede durar minutos).
    """
    settings = get_settings()
    rec = db.get(Recording, recording_id)
    if (
        rec is None
        or rec.source is not RecordingSource.IMPORT
        or rec.status is not RecordingStatus.PENDING
        or rec.audio_object_key
    ):
        return
    chunks = list(
        db.scalars(
            select(AudioSegment)
            .where(AudioSegment.recording_id == rec.id)
            .order_by(AudioSegment.track, AudioSegment.seq)
        )
    )
    target_key = audio_key(rec)
    db.rollback()  # libera la instantánea de lectura

    with tempfile.TemporaryDirectory(dir=settings.scratch_dir) as tmp:
        source = Path(tmp) / "source"
        with source.open("wb") as f:
            for chunk in chunks:
                data = storage.get(chunk.object_key)
                if hashlib.sha256(data).hexdigest() != chunk.sha256:
                    raise MediaError("corrupted_upload", chunk.object_key)
                f.write(data)

        result = scanner.scan(source)  # ScannerUnavailable: lo reintenta la tarea
        if not result.clean:
            log.warning(
                "malware_detected", recording_id=str(recording_id), signature=result.signature
            )
            mark_failed(db, redis, recording_id, "malware_detected", action=REJECTED)
            return

        info = probe(source)
        if not info.has_audio:
            mark_failed(db, redis, recording_id, "no_audio", action=REJECTED)
            return
        if info.duration_ms > settings.recording_max_duration_minutes * 60_000:
            mark_failed(db, redis, recording_id, "duration_limit", action=REJECTED)
            return

        output = Path(tmp) / "audio.m4a"
        extract_audio(source, output)
        storage.put_file(target_key, str(output), "audio/mp4")
        # La duración real es la del audio extraído (el contenedor original puede mentir).
        duration_ms = probe(output).duration_ms or info.duration_ms

    rec = db.get(Recording, recording_id, with_for_update=True)
    if rec is None or rec.status is not RecordingStatus.PENDING or rec.audio_object_key:
        return
    rec.audio_object_key = target_key
    rec.duration_ms = duration_ms
    # El original ya no se necesita: se conserva solo el audio (puede ser un vídeo de GB).
    db.execute(delete(AudioSegment).where(AudioSegment.recording_id == rec.id))
    record_audit_event(
        db,
        action="recording.import_ready",
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={"format": info.format_name, "duration_ms": duration_ms},
    )
    db.commit()
    for chunk in chunks:
        storage.delete(chunk.object_key)
    events.publish(
        redis, rec.id, {"type": "status", "status": rec.status.value, "audio_ready": True}
    )

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession, require_csrf
from app.auth.sessions import now
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.media.containers import (
    EXTENSIONS,
    IMPORT_CONTAINERS,
    MIME_TYPES,
    SEGMENT_CONTAINERS,
    sniff_container,
)
from app.models import (
    AudioSegment,
    Membership,
    Recording,
    RecordingSource,
    RecordingStatus,
    Role,
    Transcript,
    TranscriptEdit,
    TranscriptSegment,
    User,
)
from app.orgs.permissions import Action, authorize
from app.recordings import events
from app.recordings.schemas import (
    CompleteRequest,
    EnginesOut,
    MissingSegment,
    RecordingCreate,
    RecordingDetail,
    RecordingListItem,
    RecordingOut,
    RecordingUpdate,
    ReprocessRequest,
    SearchMatch,
    SegmentOut,
    SegmentUpdate,
    SpeakerNamesUpdate,
    TrackOut,
    TrackSummary,
    TranscriptEditOut,
    TranscriptOut,
    TranscriptSegmentOut,
)
from app.storage import ObjectStorage, get_storage

router = APIRouter(prefix="/v1", tags=["recordings"], dependencies=[Depends(require_csrf)])

Client = Annotated[ClientInfo, Depends(get_client_info)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
EventRedis = Annotated[Redis, Depends(events.get_event_redis)]

NOT_FOUND = AppError("not_found", "Grabación no encontrada", 404)


class TaskQueue:
    """Encolado de trabajos del worker (sustituible en tests)."""

    def process(self, recording_id: uuid.UUID) -> None:
        from app.worker.tasks import enqueue_recording

        enqueue_recording(recording_id)

    def purge_storage(self, prefix: str) -> None:
        from app.worker.tasks import purge_storage_prefix

        purge_storage_prefix.delay(prefix)


def get_task_queue() -> TaskQueue:
    return TaskQueue()


Tasks = Annotated[TaskQueue, Depends(get_task_queue)]
MAX_MISSING_REPORTED = 500


def _recording_out(rec: Recording) -> RecordingOut:
    return RecordingOut(
        id=rec.id,
        organization_id=rec.organization_id,
        owner_id=rec.owner_id,
        title=rec.title,
        language=rec.language,
        status=rec.status,
        source=rec.source,
        duration_ms=rec.duration_ms,
        size_bytes=rec.size_bytes,
        track_count=rec.track_count,
        source_filename=rec.source_filename,
        audio_ready=rec.audio_object_key is not None,
        error_code=rec.error_code,
        capture_warning=rec.capture_warning,
        transcription_engine=rec.transcription_engine,
        created_at=rec.created_at,
        completed_at=rec.completed_at,
    )


def _safe_filename(name: str | None) -> str | None:
    if not name:
        return None
    # Solo el nombre: nunca rutas del cliente.
    return name.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255] or None


def _tracks(db: Session, rec: Recording) -> list[TrackSummary]:
    rows = db.execute(
        select(
            AudioSegment.track,
            func.count(),
            func.coalesce(func.sum(AudioSegment.duration_ms), 0),
        )
        .where(AudioSegment.recording_id == rec.id)
        .group_by(AudioSegment.track)
    ).all()
    found = {track: (count, duration) for track, count, duration in rows}
    return [
        TrackSummary(track=t, segments=found.get(t, (0, 0))[0], duration_ms=found.get(t, (0, 0))[1])
        for t in range(rec.track_count)
    ]


def _detail(db: Session, rec: Recording) -> RecordingDetail:
    return RecordingDetail(**_recording_out(rec).model_dump(), tracks=_tracks(db, rec))


def load_recording(
    db: Session, user: User, recording_id: uuid.UUID, action: Action, *, for_update: bool = False
) -> Recording:
    """Aplica la autorización de la organización: 404 si el usuario no es miembro."""
    rec = db.get(Recording, recording_id, with_for_update=for_update)
    if rec is None:
        raise NOT_FOUND
    try:
        authorize(db, user.id, rec.organization_id, action)
    except AppError as exc:
        if exc.status_code == 404:
            raise NOT_FOUND from None
        raise
    return rec


def load_own_uploading(db: Session, user: User, recording_id: uuid.UUID) -> Recording:
    """Solo quien graba sube audio, si conserva permiso de creación y la captura sigue abierta."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE, for_update=True)
    if rec.owner_id != user.id:
        raise AppError("forbidden", "Solo quien graba puede añadir audio", 403)
    if rec.status is not RecordingStatus.UPLOADING:
        raise AppError("recording_closed", "La captura de esta grabación ya está cerrada", 409)
    return rec


def _snapshot(db: Session, rec: Recording) -> dict[str, object]:
    return {"type": "recording", **_detail(db, rec).model_dump(mode="json")}


# --- Sesiones de grabación -----------------------------------------------------------------


@router.post("/recordings", status_code=status.HTTP_201_CREATED)
def create_recording(
    body: RecordingCreate, user: CurrentUser, db: DbSession, limiter: Limiter, client: Client
) -> RecordingDetail:
    limiter.hit(f"recording_create:user:{user.id}", limit=30, window_seconds=3600)
    authorize(db, user.id, body.organization_id, Action.RECORDING_CREATE)
    rec = Recording(
        organization_id=body.organization_id,
        owner_id=user.id,
        title=body.title.strip(),
        language=body.language,
        status=RecordingStatus.UPLOADING,
        source=RecordingSource(body.source),
        source_filename=_safe_filename(body.filename) if body.source == "import" else None,
        track_count=1,
        duration_ms=0,
        size_bytes=0,
        consent_confirmed_at=now(),
    )
    db.add(rec)
    db.flush()
    record_audit_event(
        db,
        action="recording.create",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={"consent": True, "source": body.source},
    )
    db.commit()
    db.refresh(rec)
    return _detail(db, rec)


HIGHLIGHT = "StartSel=⟦, StopSel=⟧, MaxFragments=0, HighlightAll=TRUE"
MATCHES_PER_RECORDING = 3


def _is_latest_transcript() -> Any:
    """Condición: la fila de `transcripts` es la última versión de su grabación.

    Alias propio y correlación explícita: sin ella, la subconsulta añadía su propio FROM y
    calculaba el máximo de versión de todas las grabaciones a la vez.
    """
    latest = aliased(Transcript)
    newest = (
        select(func.max(latest.version))
        .where(latest.recording_id == Transcript.recording_id)
        .correlate_except(latest)
        .scalar_subquery()
    )
    return Transcript.version == newest


@router.get("/recordings")
def list_recordings(
    user: CurrentUser,
    db: DbSession,
    organization_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    speaker: Annotated[str | None, Query(max_length=100)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[RecordingListItem]:
    """Listado y búsqueda. `q` busca en el título y en la última transcripción; `speaker`, en
    los nombres asignados a los hablantes."""
    # El join con memberships limita el listado a organizaciones del usuario.
    stmt = (
        select(Recording)
        .join(Membership, Membership.organization_id == Recording.organization_id)
        .where(Membership.user_id == user.id)
        .order_by(Recording.created_at.desc())
        .limit(limit)
    )
    if organization_id is not None:
        stmt = stmt.where(Recording.organization_id == organization_id)
    if date_from is not None:
        stmt = stmt.where(Recording.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Recording.created_at <= date_to)

    query = None
    text_query = (q or "").strip()
    if text_query:
        query = func.websearch_to_tsquery("simple", text_query)
        in_transcript = (
            select(TranscriptSegment.id)
            .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
            .where(
                Transcript.recording_id == Recording.id,
                _is_latest_transcript(),
                func.to_tsvector("simple", TranscriptSegment.text).op("@@")(query),
            )
            .exists()
        )
        stmt = stmt.where(
            or_(Recording.title.ilike(f"%{_escape_like(text_query)}%"), in_transcript)
        )
    if speaker and speaker.strip():
        names = func.jsonb_each_text(Transcript.speaker_names).table_valued("value")
        has_speaker = (
            select(Transcript.id)
            .where(
                Transcript.recording_id == Recording.id,
                _is_latest_transcript(),
                select(names.c.value)
                .where(names.c.value.ilike(f"%{_escape_like(speaker.strip())}%"))
                .exists(),
            )
            .exists()
        )
        stmt = stmt.where(has_speaker)

    recordings = list(db.scalars(stmt))
    matches: dict[uuid.UUID, list[SearchMatch]] = {}
    if query is not None and recordings:
        rows = db.execute(
            select(
                Transcript.recording_id,
                TranscriptSegment.idx,
                TranscriptSegment.start_ms,
                func.ts_headline("simple", TranscriptSegment.text, query, HIGHLIGHT),
            )
            .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
            .where(
                Transcript.recording_id.in_([r.id for r in recordings]),
                _is_latest_transcript(),
                func.to_tsvector("simple", TranscriptSegment.text).op("@@")(query),
            )
            .order_by(Transcript.recording_id, TranscriptSegment.idx)
        ).all()
        for recording_id, idx, start_ms, highlighted in rows:
            found = matches.setdefault(recording_id, [])
            if len(found) < MATCHES_PER_RECORDING:
                found.append(SearchMatch(idx=idx, start_ms=start_ms, highlighted=highlighted))
    return [
        RecordingListItem(**_recording_out(r).model_dump(), matches=matches.get(r.id, []))
        for r in recordings
    ]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RecordingDetail:
    return _detail(db, load_recording(db, user, recording_id, Action.RECORDING_READ))


@router.patch("/recordings/{recording_id}")
def update_recording(
    recording_id: uuid.UUID,
    body: RecordingUpdate,
    user: CurrentUser,
    db: DbSession,
    client: Client,
) -> RecordingDetail:
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE)
    previous = rec.title
    rec.title = body.title.strip()
    record_audit_event(
        db,
        action="recording.renamed",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={"from": previous, "to": rec.title},
    )
    db.commit()
    return _detail(db, rec)


@router.delete("/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording(
    recording_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    tasks: Tasks,
    client: Client,
) -> None:
    """Borra la grabación con sus transcripciones, historial y documentos de IA; el audio se
    purga después en el worker. Solo quien grabó o un administrador. Irreversible."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE, for_update=True)
    _, membership = authorize(db, user.id, rec.organization_id, Action.RECORDING_READ)
    if rec.owner_id != user.id and membership.role not in (Role.OWNER, Role.ADMIN):
        raise AppError("forbidden", "Solo quien grabó o un administrador puede eliminarla", 403)
    # Mientras se transcribe, el worker escribe en la grabación y en el almacenamiento.
    if rec.status is RecordingStatus.PROCESSING:
        raise AppError("recording_busy", "Espera a que termine de procesarse", 409)
    record_audit_event(
        db,
        action="recording.deleted",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={"title": rec.title, "duration_ms": rec.duration_ms},
    )
    # El resto lo borra la base de datos en cascada (ON DELETE CASCADE); el consumo de IA se
    # conserva sin la referencia (ON DELETE SET NULL).
    db.execute(delete(Recording).where(Recording.id == rec.id))
    db.commit()
    tasks.purge_storage(f"orgs/{rec.organization_id}/recordings/{rec.id}/")


@router.post("/recordings/{recording_id}/tracks", status_code=status.HTTP_201_CREATED)
def open_track(
    recording_id: uuid.UUID, user: CurrentUser, db: DbSession, redis: EventRedis
) -> TrackOut:
    """Reanudar una captura interrumpida: el audio nuevo va a una pista nueva."""
    rec = load_own_uploading(db, user, recording_id)
    if rec.track_count >= 100:
        raise AppError("too_many_tracks", "Se ha alcanzado el máximo de reanudaciones", 409)
    track = rec.track_count
    rec.track_count += 1
    db.commit()
    events.publish(redis, rec.id, _snapshot(db, rec))
    return TrackOut(track=track)


# --- Segmentos -----------------------------------------------------------------------------


@router.post("/recordings/{recording_id}/segments", status_code=status.HTTP_201_CREATED)
def upload_segment(
    recording_id: uuid.UUID,
    response: Response,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
    redis: EventRedis,
    limiter: Limiter,
    segment_id: Annotated[uuid.UUID, Form()],
    track: Annotated[int, Form(ge=0, le=99)],
    seq: Annotated[int, Form(ge=0, le=9999)],
    duration_ms: Annotated[int, Form(ge=0)],
    sha256: Annotated[str, Form(pattern=r"^[0-9a-f]{64}$")],
    file: Annotated[UploadFile, File()],
) -> SegmentOut:
    """Subida idempotente: reenviar el mismo segmento (mismo id y checksum) devuelve 200."""
    settings = get_settings()
    limiter.hit(f"segment_upload:user:{user.id}", limit=240, window_seconds=60)

    existing = db.get(AudioSegment, segment_id)
    if existing is not None:
        load_recording(db, user, existing.recording_id, Action.RECORDING_READ)
        same = (existing.recording_id, existing.track, existing.seq, existing.sha256) == (
            recording_id,
            track,
            seq,
            sha256,
        )
        if not same:
            raise AppError("segment_conflict", "El identificador ya se usó para otro segmento", 409)
        response.status_code = status.HTTP_200_OK
        return SegmentOut(
            id=existing.id,
            track=existing.track,
            seq=existing.seq,
            sha256=existing.sha256,
            size_bytes=existing.size_bytes,
            duplicate=True,
        )

    rec = load_own_uploading(db, user, recording_id)
    imported = rec.source is RecordingSource.IMPORT
    if track >= rec.track_count:
        raise AppError("unknown_track", "La pista no existe; ábrela antes de subir audio", 409)
    if imported:
        # Bloques de un archivo: la duración real la mide el worker con ffprobe.
        duration_ms = 0
    elif duration_ms > settings.segment_max_duration_ms:
        raise AppError("segment_too_long", "El segmento es demasiado largo", 422)
    if rec.duration_ms + duration_ms > settings.recording_max_duration_minutes * 60_000:
        raise AppError("duration_limit", "Se ha alcanzado la duración máxima de la grabación", 409)

    data = file.file.read(settings.segment_max_bytes + 1)
    if len(data) > settings.segment_max_bytes:
        raise AppError("segment_too_large", "El segmento supera el tamaño máximo", 413)
    if not data:
        raise AppError("empty_segment", "El segmento está vacío", 422)
    if hashlib.sha256(data).hexdigest() != sha256:
        # Corrupción en tránsito: el cliente conserva su copia y reintenta.
        raise AppError("checksum_mismatch", "El checksum no coincide", 422)
    if imported and rec.size_bytes + len(data) > settings.import_max_bytes:
        raise AppError("import_too_large", "El archivo supera el tamaño máximo", 413)

    base = f"orgs/{rec.organization_id}/recordings/{rec.id}/tracks/{track}/{seq:05d}-{segment_id}"
    if imported and seq > 0:
        # Bloque intermedio de un archivo: no es decodificable por sí solo.
        key, mime_type = f"{base}.part", "application/octet-stream"
    else:
        container = sniff_container(data[:16])
        allowed = IMPORT_CONTAINERS if imported else SEGMENT_CONTAINERS
        if container not in allowed:
            raise AppError("unsupported_media", "Formato de audio no admitido", 415)
        key, mime_type = f"{base}.{EXTENSIONS[container]}", MIME_TYPES[container]
    storage.put(key, data, mime_type)

    segment = AudioSegment(
        id=segment_id,
        recording_id=rec.id,
        track=track,
        seq=seq,
        duration_ms=duration_ms,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256,
        object_key=key,
    )
    db.add(segment)
    rec.duration_ms += duration_ms
    rec.size_bytes += len(data)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError("segment_conflict", "Ya existe otro segmento en esa posición", 409) from None

    events.publish(
        redis,
        rec.id,
        {"type": "segment", "track": track, "seq": seq, "duration_ms": rec.duration_ms},
    )
    return SegmentOut(id=segment.id, track=track, seq=seq, sha256=sha256, size_bytes=len(data))


# --- Cierre --------------------------------------------------------------------------------


@router.post("/recordings/{recording_id}/complete")
def complete_recording(
    recording_id: uuid.UUID,
    body: CompleteRequest,
    user: CurrentUser,
    db: DbSession,
    redis: EventRedis,
    tasks: Tasks,
    client: Client,
) -> RecordingDetail:
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE, for_update=True)
    if rec.owner_id != user.id:
        raise AppError("forbidden", "Solo quien graba puede cerrar la captura", 403)
    if rec.status is RecordingStatus.PENDING:
        return _detail(db, rec)  # reintento idempotente
    if rec.status is not RecordingStatus.UPLOADING:
        raise AppError("recording_closed", "La captura de esta grabación ya está cerrada", 409)
    if any(t < 0 or t >= rec.track_count for t in body.tracks):
        raise AppError("unknown_track", "La pista no existe", 409)

    present = {
        (t, s)
        for t, s in db.execute(
            select(AudioSegment.track, AudioSegment.seq).where(AudioSegment.recording_id == rec.id)
        ).all()
    }
    missing = [
        MissingSegment(track=track, seq=seq)
        for track, count in sorted(body.tracks.items())
        for seq in range(count)
        if (track, seq) not in present
    ]
    if missing:
        raise AppError(
            "missing_segments",
            "Faltan segmentos por subir",
            409,
            details=[m.model_dump() for m in missing[:MAX_MISSING_REPORTED]],
        )
    if not present:
        raise AppError("empty_recording", "La grabación no tiene audio", 409)

    rec.status = RecordingStatus.PENDING
    rec.completed_at = now()
    record_audit_event(
        db,
        action="recording.complete",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={"segments": len(present), "duration_ms": rec.duration_ms},
    )
    db.commit()
    tasks.process(rec.id)
    detail = _detail(db, rec)
    events.publish(redis, rec.id, {"type": "recording", **detail.model_dump(mode="json")})
    return detail


# --- Transcripción ------------------------------------------------------------------------


@router.get("/recordings/{recording_id}/transcript")
def get_transcript(
    recording_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> TranscriptOut:
    """Última versión de la transcripción (o la indicada)."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    stmt = select(Transcript).where(Transcript.recording_id == rec.id)
    stmt = (
        stmt.where(Transcript.version == version)
        if version
        else stmt.order_by(Transcript.version.desc()).limit(1)
    )
    transcript = db.scalars(stmt).first()
    if transcript is None:
        raise AppError("transcript_not_found", "La transcripción aún no está disponible", 404)
    return _transcript_out(transcript)


def _transcript_out(transcript: Transcript) -> TranscriptOut:
    return TranscriptOut(
        id=transcript.id,
        recording_id=transcript.recording_id,
        version=transcript.version,
        language=transcript.language,
        provider=transcript.provider,
        model=transcript.model,
        created_at=transcript.created_at,
        speaker_names=dict(transcript.speaker_names or {}),
        segments=[
            TranscriptSegmentOut(
                idx=s.idx,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                speaker=s.speaker,
                text=s.text,
                confidence=s.confidence,
            )
            for s in transcript.segments
        ],
    )


def _latest_transcript(db: Session, recording_id: uuid.UUID) -> Transcript:
    transcript = db.scalars(
        select(Transcript)
        .where(Transcript.recording_id == recording_id)
        .order_by(Transcript.version.desc())
        .limit(1)
    ).first()
    if transcript is None:
        raise AppError("transcript_not_found", "La transcripción aún no está disponible", 404)
    return transcript


@router.patch("/recordings/{recording_id}/transcript/segments/{idx}")
def edit_segment(
    recording_id: uuid.UUID,
    idx: int,
    body: SegmentUpdate,
    user: CurrentUser,
    db: DbSession,
    client: Client,
) -> TranscriptOut:
    """Corrige el texto o el hablante de una frase de la última versión. Queda en el historial."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE)
    transcript = _latest_transcript(db, rec.id)
    segment = next((s for s in transcript.segments if s.idx == idx), None)
    if segment is None:
        raise AppError("not_found", "Frase no encontrada", 404)

    changes: list[tuple[str, str | None, str | None]] = []
    if "text" in body.model_fields_set and body.text is not None:
        text = body.text.strip()
        if text and text != segment.text:
            changes.append(("text", segment.text, text))
            segment.text = text
    if "speaker" in body.model_fields_set and body.speaker != segment.speaker:
        known = {s.speaker for s in transcript.segments if s.speaker}
        if body.speaker is not None and body.speaker not in known:
            raise AppError("unknown_speaker", "Hablante desconocido", 422)
        changes.append(("speaker", segment.speaker, body.speaker))
        segment.speaker = body.speaker
    if changes:
        db.add_all(
            TranscriptEdit(
                transcript_id=transcript.id,
                segment_idx=idx,
                field=field,
                old_value=old,
                new_value=new,
                editor_id=user.id,
            )
            for field, old, new in changes
        )
        transcript.content = "\n".join(s.text for s in transcript.segments)
        record_audit_event(
            db,
            action="transcript.edited",
            client=client,
            actor_id=user.id,
            organization_id=rec.organization_id,
            resource_type="transcript",
            resource_id=transcript.id,
            metadata={"segment": idx, "fields": [c[0] for c in changes]},
        )
        db.commit()
    return _transcript_out(transcript)


@router.get("/recordings/{recording_id}/transcript/history")
def transcript_history(
    recording_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[TranscriptEditOut]:
    """Cambios manuales de la última versión de la transcripción, del más reciente al primero."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    transcript = _latest_transcript(db, rec.id)
    rows = db.execute(
        select(TranscriptEdit, User.name)
        .outerjoin(User, User.id == TranscriptEdit.editor_id)
        .where(TranscriptEdit.transcript_id == transcript.id)
        .order_by(TranscriptEdit.created_at.desc())
        .limit(200)
    ).all()
    return [
        TranscriptEditOut(
            segment_idx=e.segment_idx,
            field=e.field,
            old_value=e.old_value,
            new_value=e.new_value,
            editor_name=name,
            created_at=e.created_at,
        )
        for e, name in rows
    ]


@router.patch("/recordings/{recording_id}/transcript/speakers")
def rename_speakers(
    recording_id: uuid.UUID,
    body: SpeakerNamesUpdate,
    user: CurrentUser,
    db: DbSession,
    client: Client,
) -> TranscriptOut:
    """Da nombre a los hablantes detectados de la última versión de la transcripción."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE)
    transcript = _latest_transcript(db, rec.id)
    known = {s.speaker for s in transcript.segments if s.speaker}
    unknown = set(body.names) - known
    if unknown:
        raise AppError("unknown_speaker", "Hablante desconocido", 422, details=sorted(unknown))
    names = dict(transcript.speaker_names or {})
    for label, name in body.names.items():
        name = name.strip()[:100]
        if name:
            names[label] = name
        else:
            names.pop(label, None)
    # Asignación nueva (no mutación): SQLAlchemy detecta el cambio del JSONB.
    transcript.speaker_names = names
    record_audit_event(
        db,
        action="transcript.speakers_renamed",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="transcript",
        resource_id=transcript.id,
        metadata={"labels": sorted(body.names)},
    )
    db.commit()
    return _transcript_out(transcript)


@router.post("/recordings/{recording_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
def reprocess_recording(
    recording_id: uuid.UUID,
    body: ReprocessRequest,
    user: CurrentUser,
    db: DbSession,
    redis: EventRedis,
    tasks: Tasks,
    limiter: Limiter,
    client: Client,
) -> RecordingDetail:
    """Vuelve a transcribir (p. ej. con otro idioma). Crea una versión nueva; la anterior se
    conserva. Solo quien grabó o un administrador: tiene coste de procesamiento."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE, for_update=True)
    _, membership = authorize(db, user.id, rec.organization_id, Action.RECORDING_READ)
    if rec.owner_id != user.id and membership.role not in (Role.OWNER, Role.ADMIN):
        raise AppError("forbidden", "Solo quien grabó o un administrador puede reprocesar", 403)
    limiter.hit(f"reprocess:recording:{rec.id}", limit=5, window_seconds=3600)
    if rec.status not in (RecordingStatus.TRANSCRIBED, RecordingStatus.FAILED):
        raise AppError("not_reprocessable", "La grabación no se puede reprocesar ahora", 409)
    has_audio = rec.audio_object_key is not None or (
        rec.source is RecordingSource.BROWSER
        and db.scalar(select(func.count()).where(AudioSegment.recording_id == rec.id))
    )
    if not has_audio:
        raise AppError("not_reprocessable", "La grabación no tiene audio procesable", 409)

    previous = rec.language
    if body.language:
        rec.language = body.language
    if body.engine:
        if body.engine not in get_settings().available_engines:
            raise AppError(
                "engine_unavailable", "Ese motor de transcripción no está disponible", 422
            )
        rec.transcription_engine = body.engine
    rec.status = RecordingStatus.PENDING
    rec.error_code = None
    record_audit_event(
        db,
        action="recording.reprocess",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={
            "language_from": previous,
            "language_to": rec.language,
            "engine": rec.transcription_engine,
        },
    )
    db.commit()
    tasks.process(rec.id)
    detail = _detail(db, rec)
    events.publish(redis, rec.id, {"type": "recording", **detail.model_dump(mode="json")})
    return detail


@router.get("/transcription-engines")
def transcription_engines(user: CurrentUser) -> EnginesOut:
    """Motores de transcripción disponibles en este despliegue."""
    settings = get_settings()
    return EnginesOut(
        default=settings.default_engine,
        available=settings.available_engines,
        local_speakers=settings.local_diarization,
    )


# --- Eventos (SSE) -------------------------------------------------------------------------


def _readable_snapshot(
    recording_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> dict[str, object]:
    # Dependencia síncrona: FastAPI la ejecuta en el threadpool, sin bloquear el event loop.
    snapshot = _snapshot(db, load_recording(db, user, recording_id, Action.RECORDING_READ))
    # La sesión vive lo que dure el stream (minutos u horas): se cierra la transacción ya
    # para no retener la conexión ni bloqueos (impedirían, p. ej., migraciones).
    db.rollback()
    return snapshot


HEARTBEAT_SECONDS = 15.0


@router.get("/recordings/{recording_id}/events", response_class=StreamingResponse)
async def recording_events(
    recording_id: uuid.UUID,
    request: Request,
    snapshot: Annotated[dict[str, object], Depends(_readable_snapshot)],
    aredis: Annotated[AsyncRedis, Depends(events.get_async_redis)],
) -> StreamingResponse:
    """Server-Sent Events: estado inicial y cambios de la grabación (segmentos, estado)."""

    async def stream() -> AsyncIterator[str]:
        pubsub = aredis.pubsub()
        await pubsub.subscribe(events.channel(recording_id))
        try:
            yield f"event: recording\ndata: {json.dumps(snapshot, default=str)}\n\n"
            last_sent = time.monotonic()
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None:
                    data = message["data"]
                    payload = data.decode() if isinstance(data, bytes) else str(data)
                    kind = json.loads(payload).get("type", "message")
                    yield f"event: {kind}\ndata: {payload}\n\n"
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent > HEARTBEAT_SECONDS:
                    yield ": ping\n\n"
                    last_sent = time.monotonic()
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

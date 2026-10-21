import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession, require_csrf
from app.auth.sessions import now
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.models import AudioSegment, Membership, Recording, RecordingStatus, User
from app.orgs.permissions import Action, authorize
from app.recordings import events
from app.recordings.media import EXTENSIONS, MIME_TYPES, SEGMENT_CONTAINERS, sniff_container
from app.recordings.schemas import (
    CompleteRequest,
    MissingSegment,
    RecordingCreate,
    RecordingDetail,
    RecordingOut,
    SegmentOut,
    TrackOut,
    TrackSummary,
)
from app.storage import ObjectStorage, get_storage

router = APIRouter(prefix="/v1", tags=["recordings"], dependencies=[Depends(require_csrf)])

Client = Annotated[ClientInfo, Depends(get_client_info)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
EventRedis = Annotated[Redis, Depends(events.get_event_redis)]

NOT_FOUND = AppError("not_found", "Grabación no encontrada", 404)
MAX_MISSING_REPORTED = 500


def _recording_out(rec: Recording) -> RecordingOut:
    return RecordingOut.model_validate(rec, from_attributes=True)


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
        track_count=1,
        duration_ms=0,
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
        metadata={"consent": True},
    )
    db.commit()
    db.refresh(rec)
    return _detail(db, rec)


@router.get("/recordings")
def list_recordings(
    user: CurrentUser,
    db: DbSession,
    organization_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[RecordingOut]:
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
    return [_recording_out(r) for r in db.scalars(stmt)]


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RecordingDetail:
    return _detail(db, load_recording(db, user, recording_id, Action.RECORDING_READ))


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
    if track >= rec.track_count:
        raise AppError("unknown_track", "La pista no existe; ábrela antes de subir audio", 409)
    if duration_ms > settings.segment_max_duration_ms:
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
    container = sniff_container(data[:16])
    if container not in SEGMENT_CONTAINERS:
        raise AppError("unsupported_media", "Formato de audio no admitido", 415)

    key = (
        f"orgs/{rec.organization_id}/recordings/{rec.id}/tracks/{track}/"
        f"{seq:05d}-{segment_id}.{EXTENSIONS[container]}"
    )
    storage.put(key, data, MIME_TYPES[container])

    segment = AudioSegment(
        id=segment_id,
        recording_id=rec.id,
        track=track,
        seq=seq,
        duration_ms=duration_ms,
        mime_type=MIME_TYPES[container],
        size_bytes=len(data),
        sha256=sha256,
        object_key=key,
    )
    db.add(segment)
    rec.duration_ms += duration_ms
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
    detail = _detail(db, rec)
    events.publish(redis, rec.id, {"type": "recording", **detail.model_dump(mode="json")})
    return detail


# --- Eventos (SSE) -------------------------------------------------------------------------


def _readable_snapshot(
    recording_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> dict[str, object]:
    # Dependencia síncrona: FastAPI la ejecuta en el threadpool, sin bloquear el event loop.
    return _snapshot(db, load_recording(db, user, recording_id, Action.RECORDING_READ))


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

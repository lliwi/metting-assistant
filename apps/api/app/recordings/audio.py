"""Reproducción del audio de una grabación con soporte de Range (saltar a cualquier punto)."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from redis import Redis

from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.core.request_context import ClientInfo, get_client_info
from app.orgs.permissions import Action
from app.recordings import events
from app.recordings.router import load_recording
from app.storage import ObjectStorage, get_storage

router = APIRouter(prefix="/v1", tags=["recordings"])

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
# El reproductor pide muchos trozos: se audita el primer acceso de cada usuario cada 12 h.
AUDIT_WINDOW_SECONDS = 12 * 3600


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Un único rango "bytes=a-b", "bytes=a-" o "bytes=-n". None si no hay cabecera."""
    if not header:
        return None
    match = _RANGE.match(header.strip())
    if not match or not (match.group(1) or match.group(2)):
        raise ValueError(header)
    first, last = match.group(1), match.group(2)
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        start = max(0, size - int(last))
        end = size - 1
    if start > end or start >= size:
        raise ValueError(header)
    return start, end


@router.get("/recordings/{recording_id}/audio", response_class=StreamingResponse)
def stream_audio(
    recording_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    redis: Annotated[Redis, Depends(events.get_event_redis)],
    client: Annotated[ClientInfo, Depends(get_client_info)],
) -> Response:
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    if not rec.audio_object_key:
        raise AppError("audio_not_ready", "El audio aún no está disponible", 404)
    key = rec.audio_object_key

    if redis.set(f"audio_audit:{user.id}:{rec.id}", "1", nx=True, ex=AUDIT_WINDOW_SECONDS):
        record_audit_event(
            db,
            action="recording.audio_accessed",
            client=client,
            actor_id=user.id,
            organization_id=rec.organization_id,
            resource_type="recording",
            resource_id=rec.id,
        )
        db.commit()
    # La respuesta puede durar minutos: no se retiene ninguna transacción mientras tanto.
    db.rollback()

    size, content_type = storage.head(key)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
    try:
        requested = parse_range(request.headers.get("range"), size)
    except ValueError:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    start, end = requested or (0, size - 1)
    headers["Content-Length"] = str(end - start + 1)
    if requested:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        storage.iter_range(key, start, end),
        status_code=206 if requested else 200,
        media_type=content_type,
        headers=headers,
    )

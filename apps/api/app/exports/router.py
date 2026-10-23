import re
import unicodedata
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select

from app.ai.service import latest_transcript
from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.exports.builders import FORMATS, ExportContext, Segment
from app.models import AiOutput, AiOutputKind, AiOutputStatus
from app.orgs.permissions import Action
from app.recordings.router import load_recording

router = APIRouter(prefix="/v1", tags=["exports"])

Format = Literal["md", "txt", "pdf", "docx", "srt", "vtt"]
# Formatos que solo contienen la transcripción con marcas de tiempo.
TRANSCRIPT_ONLY = {"txt", "srt", "vtt"}


def filename(title: str, extension: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_title).strip("-").lower()[:80]
    return f"{slug or 'grabacion'}.{extension}"


@router.get("/recordings/{recording_id}/exports/{fmt}")
def export_recording(
    recording_id: uuid.UUID,
    fmt: Format,
    user: CurrentUser,
    db: DbSession,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    client: Annotated[ClientInfo, Depends(get_client_info)],
    transcript: bool = True,
    summary: bool = True,
    minutes: bool = True,
) -> Response:
    """Descarga la grabación en el formato indicado. Resumen y acta se incluyen si existen."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    limiter.hit(f"export:user:{user.id}", limit=60, window_seconds=3600)

    latest = latest_transcript(db, rec.id)
    ctx = ExportContext(
        title=rec.title,
        created_at=rec.created_at,
        duration_ms=rec.duration_ms,
        locale=user.locale,
        speaker_names=dict(latest.speaker_names or {}) if latest else {},
    )
    if latest and (transcript or fmt in TRANSCRIPT_ONLY):
        ctx.segments = [Segment(s.start_ms, s.end_ms, s.speaker, s.text) for s in latest.segments]
    if fmt not in TRANSCRIPT_ONLY:
        documents: dict[AiOutputKind, AiOutput] = {}
        for output in db.scalars(
            select(AiOutput)
            .where(AiOutput.recording_id == rec.id, AiOutput.status == AiOutputStatus.READY)
            .order_by(AiOutput.created_at.desc())
        ):
            documents.setdefault(output.kind, output)
        if summary and AiOutputKind.SUMMARY in documents:
            ctx.summary = documents[AiOutputKind.SUMMARY].content
        if minutes and AiOutputKind.MINUTES in documents:
            ctx.minutes = documents[AiOutputKind.MINUTES].content

    if not (ctx.segments or ctx.summary or ctx.minutes):
        raise AppError("nothing_to_export", "No hay contenido para exportar", 409)

    build, media_type = FORMATS[fmt]
    body = build(ctx)
    record_audit_event(
        db,
        action="recording.exported",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="recording",
        resource_id=rec.id,
        metadata={
            "format": fmt,
            "transcript": bool(ctx.segments),
            "summary": ctx.summary is not None,
            "minutes": ctx.minutes is not None,
        },
    )
    db.commit()
    name = filename(rec.title, fmt)
    return Response(
        body,
        media_type=media_type,
        headers={
            # El nombre ya está normalizado a ASCII (sin acentos ni comillas).
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-store",
        },
    )

import re
import unicodedata
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select

from app.ai.document_translation import ready_document_translations
from app.ai.service import latest_transcript
from app.ai.translation import find_translation, translated_text
from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.exports.builders import FORMATS, ExportContext, Segment
from app.media.gaps import located_gaps
from app.models import AiOutput, AiOutputKind, AiOutputStatus
from app.orgs.permissions import Action
from app.recordings.router import load_recording
from app.recordings.schemas import Language

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
    class_notes: bool = True,
    language: Language | None = None,
) -> Response:
    """Descarga la grabación en el formato indicado. Resumen, acta y apuntes se incluyen si
    existen. `language`: transcripción traducida a ese idioma (debe existir la traducción) y
    los documentos que estén traducidos a él; los que no, en el idioma original."""
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
        texts: dict[int, str] = {}
        if language and language != latest.language:
            translation = find_translation(db, latest.id, language)
            if translation is None or translation.status is not AiOutputStatus.READY:
                raise AppError("translation_not_ready", "Esa traducción no está disponible", 409)
            texts = translated_text(translation)
            ctx.translated_to = language
        # Una frase sin traducir se exporta en el idioma original.
        ctx.segments = [
            Segment(s.start_ms, s.end_ms, s.speaker, texts.get(s.idx, s.text))
            for s in latest.segments
        ]
        ctx.gaps = located_gaps(rec.audio_gaps)
    original_language = latest.language if latest else rec.language
    if fmt not in TRANSCRIPT_ONLY:
        documents: dict[AiOutputKind, AiOutput] = {}
        for output in db.scalars(
            select(AiOutput)
            .where(AiOutput.recording_id == rec.id, AiOutput.status == AiOutputStatus.READY)
            .order_by(AiOutput.created_at.desc())
        ):
            documents.setdefault(output.kind, output)
        contents = {kind: o.content for kind, o in documents.items()}
        if language and language != original_language:
            translated = ready_document_translations(db, list(documents.values()), language)
            for kind, output in documents.items():
                if output.id in translated:
                    contents[kind] = translated[output.id].content
                    ctx.translated_documents.add(kind.value)
            if ctx.translated_documents:
                ctx.documents_language = language
        if summary and AiOutputKind.SUMMARY in documents:
            ctx.summary = contents[AiOutputKind.SUMMARY]
        if minutes and AiOutputKind.MINUTES in documents:
            ctx.minutes = contents[AiOutputKind.MINUTES]
        if class_notes and AiOutputKind.CLASS_NOTES in documents:
            ctx.class_notes = contents[AiOutputKind.CLASS_NOTES]

    if not (ctx.segments or ctx.summary or ctx.minutes or ctx.class_notes):
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
            "class_notes": ctx.class_notes is not None,
            **({"language": ctx.output_language} if ctx.output_language else {}),
        },
    )
    db.commit()
    name = filename(f"{rec.title} {ctx.output_language or ''}", fmt)
    return Response(
        body,
        media_type=media_type,
        headers={
            # El nombre ya está normalizado a ASCII (sin acentos ni comillas).
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-store",
        },
    )

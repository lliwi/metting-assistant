import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.ai.service import latest_transcript
from app.ai.translation import (
    TRANSLATION_PROMPT_VERSION,
    find_translation,
    is_current,
    translated_text,
)
from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession, require_csrf
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.models import AiOutputStatus, RecordingStatus, Transcript, TranscriptTranslation
from app.orgs.permissions import Action
from app.recordings.router import load_recording
from app.recordings.schemas import Language

router = APIRouter(prefix="/v1", tags=["translations"], dependencies=[Depends(require_csrf)])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Client = Annotated[ClientInfo, Depends(get_client_info)]


class TranslationRequest(BaseModel):
    language: Language


class TranslationOut(BaseModel):
    """Estado de una traducción de la última versión de la transcripción."""

    id: uuid.UUID
    transcript_id: uuid.UUID
    language: str
    status: AiOutputStatus
    provider: str | None
    model: str | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    # Frases con texto, y cuántas tienen la traducción al día.
    total: int
    translated: int


class TranslatedSegmentOut(BaseModel):
    idx: int
    text: str
    # El original se ha corregido después de traducir esta frase.
    outdated: bool


class TranslationDetail(TranslationOut):
    segments: list[TranslatedSegmentOut]


class TaskQueue:
    def translate(self, translation_id: uuid.UUID) -> None:
        from app.worker.tasks import request_translation

        request_translation(translation_id)


def get_translation_queue() -> TaskQueue:
    return TaskQueue()


Tasks = Annotated[TaskQueue, Depends(get_translation_queue)]


def _out(translation: TranscriptTranslation, transcript: Transcript) -> TranslationOut:
    spoken = [s for s in transcript.segments if s.text.strip()]
    return TranslationOut(
        id=translation.id,
        transcript_id=translation.transcript_id,
        language=translation.language,
        status=translation.status,
        provider=translation.provider,
        model=translation.model,
        error_code=translation.error_code,
        created_at=translation.created_at,
        completed_at=translation.completed_at,
        total=len(spoken),
        translated=sum(is_current(translation, s) for s in spoken),
    )


@router.get("/recordings/{recording_id}/translations")
def list_translations(
    recording_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[TranslationOut]:
    """Traducciones de la última versión de la transcripción (sin el texto)."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    transcript = latest_transcript(db, rec.id)
    if transcript is None:
        return []
    rows = db.scalars(
        select(TranscriptTranslation)
        .where(TranscriptTranslation.transcript_id == transcript.id)
        .order_by(TranscriptTranslation.language)
    )
    return [_out(t, transcript) for t in rows]


@router.get("/recordings/{recording_id}/translations/{language}")
def get_translation(
    recording_id: uuid.UUID, language: Language, user: CurrentUser, db: DbSession
) -> TranslationDetail:
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    transcript = latest_transcript(db, rec.id)
    translation = find_translation(db, transcript.id, language) if transcript else None
    if transcript is None or translation is None:
        raise AppError("translation_not_found", "No hay traducción a ese idioma", 404)
    texts = translated_text(translation)
    return TranslationDetail(
        **_out(translation, transcript).model_dump(),
        segments=[
            TranslatedSegmentOut(
                idx=s.idx, text=texts[s.idx], outdated=not is_current(translation, s)
            )
            for s in transcript.segments
            if s.idx in texts
        ],
    )


@router.post("/recordings/{recording_id}/translations", status_code=status.HTTP_202_ACCEPTED)
def request_translation(
    recording_id: uuid.UUID,
    body: TranslationRequest,
    user: CurrentUser,
    db: DbSession,
    tasks: Tasks,
    limiter: Limiter,
    client: Client,
) -> TranslationOut:
    """Traduce la última versión de la transcripción. Si ya estaba traducida, completa las
    frases que faltan o han cambiado; si no hay ninguna, la traduce de nuevo entera."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE)
    limiter.hit(f"translation:user:{user.id}", limit=30, window_seconds=3600)
    if rec.status is not RecordingStatus.TRANSCRIBED:
        raise AppError("not_transcribed", "La grabación aún no está transcrita", 409)
    transcript = latest_transcript(db, rec.id)
    if transcript is None or not any(s.text.strip() for s in transcript.segments):
        raise AppError("empty_transcript", "La transcripción no tiene texto", 409)
    if body.language == transcript.language:
        raise AppError("same_language", "La transcripción ya está en ese idioma", 422)

    # Dos peticiones a la vez: solo una crea la fila; la otra espera y la encuentra pendiente.
    created = db.scalar(
        insert(TranscriptTranslation)
        .values(
            id=uuid.uuid4(),
            transcript_id=transcript.id,
            language=body.language,
            status=AiOutputStatus.PENDING,
            prompt_version=TRANSLATION_PROMPT_VERSION,
            segments={},
            requested_by_id=user.id,
        )
        .on_conflict_do_nothing(index_elements=["transcript_id", "language"])
        .returning(TranscriptTranslation.id)
    )
    translation = find_translation(db, transcript.id, body.language, for_update=True)
    assert translation is not None
    if created is None:
        if translation.status is AiOutputStatus.PENDING:
            raise AppError("already_translating", "Ya se está traduciendo", 409)
        spoken = [s for s in transcript.segments if s.text.strip()]
        if all(is_current(translation, s) for s in spoken):
            translation.segments = {}
        translation.status = AiOutputStatus.PENDING
        translation.error_code = None
        translation.completed_at = None
        translation.prompt_version = TRANSLATION_PROMPT_VERSION
        translation.requested_by_id = user.id
    record_audit_event(
        db,
        action="translation.requested",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="transcript_translation",
        resource_id=translation.id,
        metadata={"language": body.language},
    )
    db.commit()
    db.refresh(translation)
    tasks.translate(translation.id)
    return _out(translation, transcript)

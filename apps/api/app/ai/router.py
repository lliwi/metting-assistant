import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.service import create_outputs, latest_transcript
from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession, require_csrf
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.models import AiOutput, AiOutputKind, AiOutputStatus, RecordingStatus
from app.orgs.permissions import Action
from app.recordings.router import load_recording

router = APIRouter(prefix="/v1", tags=["ai"], dependencies=[Depends(require_csrf)])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Client = Annotated[ClientInfo, Depends(get_client_info)]


class AiOutputOut(BaseModel):
    id: uuid.UUID
    kind: AiOutputKind
    status: AiOutputStatus
    transcript_id: uuid.UUID
    provider: str | None
    model: str | None
    prompt_version: str
    # Resumen: {resumen, puntos_clave[]}. Acta: {titulo, asistentes[], temas[], decisiones[],
    # tareas[], preguntas_abiertas[]}. Cada "segmentos" es [{idx, start_ms}].
    content: dict[str, Any]
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class AiOutputRequest(BaseModel):
    kind: AiOutputKind


class TaskQueue:
    def generate(self, output_id: uuid.UUID) -> None:
        from app.worker.tasks import generate_ai_output_task

        generate_ai_output_task.delay(str(output_id))


def get_ai_task_queue() -> TaskQueue:
    return TaskQueue()


Tasks = Annotated[TaskQueue, Depends(get_ai_task_queue)]


def _out(o: AiOutput) -> AiOutputOut:
    return AiOutputOut.model_validate(o, from_attributes=True)


@router.get("/recordings/{recording_id}/ai-outputs")
def list_ai_outputs(recording_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[AiOutputOut]:
    """El documento más reciente de cada tipo."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_READ)
    latest: dict[AiOutputKind, AiOutput] = {}
    for output in db.scalars(
        select(AiOutput).where(AiOutput.recording_id == rec.id).order_by(AiOutput.created_at.desc())
    ):
        latest.setdefault(output.kind, output)
    return [_out(latest[k]) for k in AiOutputKind if k in latest]


@router.post("/recordings/{recording_id}/ai-outputs", status_code=status.HTTP_202_ACCEPTED)
def request_ai_output(
    recording_id: uuid.UUID,
    body: AiOutputRequest,
    user: CurrentUser,
    db: DbSession,
    tasks: Tasks,
    limiter: Limiter,
    client: Client,
) -> AiOutputOut:
    """Genera (o regenera) un resumen o un acta con la última versión de la transcripción."""
    rec = load_recording(db, user, recording_id, Action.RECORDING_CREATE)
    limiter.hit(f"ai_output:user:{user.id}", limit=30, window_seconds=3600)
    if rec.status is not RecordingStatus.TRANSCRIBED:
        raise AppError("not_transcribed", "La grabación aún no está transcrita", 409)
    transcript = latest_transcript(db, rec.id)
    if transcript is None or not transcript.segments:
        raise AppError("empty_transcript", "La transcripción no tiene texto", 409)
    pending = db.scalars(
        select(AiOutput.id).where(
            AiOutput.recording_id == rec.id,
            AiOutput.kind == body.kind,
            AiOutput.status == AiOutputStatus.PENDING,
        )
    ).first()
    if pending:
        raise AppError("already_generating", "Ya se está generando", 409)

    [output] = create_outputs(db, rec, transcript, [body.kind], requested_by=user.id)
    record_audit_event(
        db,
        action="ai_output.requested",
        client=client,
        actor_id=user.id,
        organization_id=rec.organization_id,
        resource_type="ai_output",
        resource_id=output.id,
        metadata={"kind": body.kind.value},
    )
    db.commit()
    db.refresh(output)
    tasks.generate(output.id)
    return _out(output)

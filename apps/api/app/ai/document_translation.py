"""Traducción de los documentos de IA (resumen, acta, apuntes).

Se traduce el documento ya generado, no se vuelve a generar: solo cambian los textos, así que
la estructura y las citas a la transcripción ("segmentos") son las mismas que en el original.
"""

import copy
import hashlib
import json
import uuid
from typing import Any

import structlog
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.documents import Line
from app.ai.llm import LlmError, LlmProvider
from app.ai.translation import DOCUMENT_SUBJECT, translate
from app.ai.usage import record_usage
from app.audit import record_audit_event
from app.auth.sessions import now
from app.core.config import get_settings
from app.models import (
    AiOutput,
    AiOutputStatus,
    AiOutputTranslation,
    AuditResult,
    Recording,
    Transcript,
)
from app.recordings import events

log = structlog.get_logger(__name__)

DOCUMENT_TRANSLATION_PROMPT_VERSION = "2026-11-27.1"

# Claves que no son texto: citas a la transcripción.
NOT_TEXT = frozenset({"segmentos"})

Path = tuple[str | int, ...]


def content_fingerprint(content: dict[str, Any]) -> str:
    """Huella del contenido (sin las marcas de tiempo, que no se traducen)."""
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def texts(content: Any, path: Path = ()) -> list[tuple[Path, str]]:
    """Textos traducibles del documento, con su posición, en orden de lectura."""
    if isinstance(content, dict):
        return [
            item
            for key, value in content.items()
            if key not in NOT_TEXT
            for item in texts(value, (*path, key))
        ]
    if isinstance(content, list):
        return [item for i, value in enumerate(content) for item in texts(value, (*path, i))]
    if isinstance(content, str) and content.strip():
        return [(path, content)]
    return []


def with_texts(content: dict[str, Any], replaced: dict[Path, str]) -> dict[str, Any]:
    out = copy.deepcopy(content)
    for path, value in replaced.items():
        target: Any = out
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return out


def is_current(translation: AiOutputTranslation, output: AiOutput) -> bool:
    return translation.source == content_fingerprint(output.content)


def find_document_translation(
    db: Session, output_id: uuid.UUID, language: str, *, for_update: bool = False
) -> AiOutputTranslation | None:
    stmt = select(AiOutputTranslation).where(
        AiOutputTranslation.output_id == output_id, AiOutputTranslation.language == language
    )
    return db.scalars(stmt.with_for_update() if for_update else stmt).first()


def ready_document_translations(
    db: Session, outputs: list[AiOutput], language: str
) -> dict[uuid.UUID, AiOutputTranslation]:
    """Traducciones listas y al día de esos documentos a un idioma (para exportar)."""
    if not outputs:
        return {}
    by_id = {o.id: o for o in outputs}
    rows = db.scalars(
        select(AiOutputTranslation).where(
            AiOutputTranslation.output_id.in_(by_id),
            AiOutputTranslation.language == language,
            AiOutputTranslation.status == AiOutputStatus.READY,
        )
    )
    return {t.output_id: t for t in rows if is_current(t, by_id[t.output_id])}


def _publish(redis: Redis, recording_id: uuid.UUID, translation: AiOutputTranslation) -> None:
    events.publish(
        redis,
        recording_id,
        {
            "type": "translation",
            "document": str(translation.output_id),
            "language": translation.language,
            "status": translation.status.value,
        },
    )


def mark_document_translation_failed(
    db: Session, redis: Redis, translation_id: uuid.UUID, code: str
) -> None:
    translation = db.get(AiOutputTranslation, translation_id, with_for_update=True)
    if translation is None or translation.status is not AiOutputStatus.PENDING:
        return
    translation.status = AiOutputStatus.FAILED
    translation.error_code = code
    translation.completed_at = now()
    output = db.get_one(AiOutput, translation.output_id)
    rec = db.get_one(Recording, output.recording_id)
    record_audit_event(
        db,
        action="document_translation.failed",
        result=AuditResult.FAILURE,
        organization_id=rec.organization_id,
        resource_type="ai_output_translation",
        resource_id=translation.id,
        metadata={"kind": output.kind.value, "language": translation.language, "reason": code},
    )
    db.commit()
    _publish(redis, rec.id, translation)


def run_document_translation(
    db: Session, llm: LlmProvider, redis: Redis, translation_id: uuid.UUID
) -> None:
    """Traduce el documento entero. Idempotente. LlmError transitorio: lo reintenta la tarea."""
    translation = db.get(AiOutputTranslation, translation_id)
    if translation is None or translation.status is not AiOutputStatus.PENDING:
        return
    output = db.get_one(AiOutput, translation.output_id)
    transcript = db.get_one(Transcript, output.transcript_id)
    recording_id = output.recording_id
    source, target = transcript.language, translation.language
    original = copy.deepcopy(output.content)
    found = texts(original)
    db.rollback()  # sin transacción abierta mientras el modelo trabaja

    lines = [Line(i, 0, None, text) for i, (_, text) in enumerate(found)]
    context = get_settings().ollama_num_ctx if llm.name == "ollama" else 100_000
    usage_start = llm.usage.copy()
    done: dict[int, str] = {}
    try:
        for part in translate(
            llm, lines, lines, {}, source, target,
            context_tokens=context, subject=DOCUMENT_SUBJECT,
        ):  # fmt: skip
            done |= part
    finally:
        usage = llm.usage.since(usage_start)
        rec = db.get(Recording, recording_id)
        if rec is not None and (usage.input_tokens or usage.output_tokens):
            record_usage(
                db,
                organization_id=rec.organization_id,
                recording_id=rec.id,
                kind="translation",
                provider=llm.name,
                model=llm.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            db.commit()

    if found and not done:
        raise LlmError("El modelo no ha devuelto ningún texto traducido", transient=False)
    translation = db.get(AiOutputTranslation, translation_id, with_for_update=True)
    if translation is None or translation.status is not AiOutputStatus.PENDING:
        db.rollback()
        return  # grabación eliminada mientras se traducía
    # Lo que el modelo no devuelva queda en el idioma original.
    translation.content = with_texts(original, {found[i][0]: t for i, t in done.items()})
    translation.source = content_fingerprint(original)
    translation.provider, translation.model = llm.name, llm.model
    translation.status = AiOutputStatus.READY
    translation.error_code = None
    translation.completed_at = now()
    rec = db.get_one(Recording, recording_id)
    record_audit_event(
        db,
        action="document_translation.generated",
        organization_id=rec.organization_id,
        resource_type="ai_output_translation",
        resource_id=translation.id,
        metadata={
            "kind": output.kind.value,
            "language": target,
            "provider": llm.name,
            "model": llm.model,
            "prompt_version": translation.prompt_version,
            "texts": len(found),
            "untranslated": len(found) - len(done),
        },
    )
    db.commit()
    log.info(
        "document_translation_ready",
        translation_id=str(translation_id),
        language=target,
        untranslated=len(found) - len(done),
    )
    _publish(redis, recording_id, translation)

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, str_enum


class AiOutputKind(StrEnum):
    SUMMARY = "summary"
    MINUTES = "minutes"


class AiOutputStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class AiOutput(UUIDPrimaryKey, Timestamps, Base):
    """Documento generado por IA a partir de una versión concreta de la transcripción.

    Se guarda el proveedor, el modelo y la versión del prompt: el resultado es trazable y
    reproducible. Regenerar crea un registro nuevo; se muestra el último de cada tipo.
    """

    __tablename__ = "ai_outputs"
    __table_args__ = (Index("ix_ai_outputs_recording_kind", "recording_id", "kind", "created_at"),)

    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE")
    )
    kind: Mapped[AiOutputKind] = mapped_column(str_enum(AiOutputKind, "ai_output_kind"))
    status: Mapped[AiOutputStatus] = mapped_column(
        str_enum(AiOutputStatus, "ai_output_status"), default=AiOutputStatus.PENDING
    )
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Última edición manual (None si es tal cual lo generó el modelo).
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class AiOutputRevision(UUIDPrimaryKey, Base):
    """Contenido anterior a cada edición manual de un documento de IA."""

    __tablename__ = "ai_output_revisions"
    __table_args__ = (Index("ix_ai_output_revisions_output", "output_id", "created_at"),)

    output_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_outputs.id", ondelete="CASCADE"))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Quién hizo el cambio que reemplazó este contenido.
    edited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

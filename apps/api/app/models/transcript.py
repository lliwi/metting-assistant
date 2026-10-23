import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey


class Transcript(UUIDPrimaryKey, Base):
    """Una transcripción de una grabación. Reprocesar crea una versión nueva."""

    __tablename__ = "transcripts"
    __table_args__ = (
        Index("uq_transcripts_recording_version", "recording_id", "version", unique=True),
    )

    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    # Texto completo (concatenación de segmentos) para exportar y para el LLM.
    content: Mapped[str] = mapped_column(Text)
    # Nombres asignados a los hablantes detectados: {"H1": "Laura", "H2": "Pau"}.
    speaker_names: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Tiempo de proceso del ASR: base del seguimiento de coste por trabajo.
    processing_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript", order_by="TranscriptSegment.idx"
    )


class TranscriptSegment(UUIDPrimaryKey, Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        Index("uq_transcript_segments_idx", "transcript_id", "idx", unique=True),
        # Búsqueda por texto transcrito. "simple" (sin stemming) sirve para es/ca/en a la vez.
        Index(
            "ix_transcript_segments_fts",
            text("to_tsvector('simple', text)"),
            postgresql_using="gin",
        ),
    )

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE")
    )
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    # Confianza 0..1 cuando el proveedor la aporta.
    confidence: Mapped[float | None] = mapped_column(Float)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


class TranscriptEdit(UUIDPrimaryKey, Base):
    """Historial de cambios manuales de una transcripción (valor anterior y nuevo)."""

    __tablename__ = "transcript_edits"
    __table_args__ = (Index("ix_transcript_edits_transcript", "transcript_id", "created_at"),)

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE")
    )
    segment_idx: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(16))  # text | speaker
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    editor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

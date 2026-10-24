import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class UsageRecord(UUIDPrimaryKey, Base):
    """Consumo de un trabajo de IA (transcripción o documento) y su coste estimado.

    Se conserva aunque se borre la grabación: es el historial de gasto de la organización."""

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_records_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    recording_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL")
    )
    # "transcription" | "summary" | "minutes"
    kind: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    audio_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    processing_ms: Mapped[int] = mapped_column(Integer, default=0)
    # USD. None = sin precio configurado para ese modelo (AI_PRICES).
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

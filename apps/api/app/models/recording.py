import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPrimaryKey, str_enum


class RecordingStatus(StrEnum):
    UPLOADING = "uploading"  # captura en curso o segmentos pendientes
    PENDING = "pending"  # captura cerrada, en cola de procesamiento
    PROCESSING = "processing"
    TRANSCRIBED = "transcribed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecordingSource(StrEnum):
    BROWSER = "browser"
    IMPORT = "import"


class Recording(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "recordings"
    __table_args__ = (Index("ix_recordings_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(8), default="es")
    status: Mapped[RecordingStatus] = mapped_column(
        str_enum(RecordingStatus, "recording_status"), default=RecordingStatus.UPLOADING
    )
    source: Mapped[RecordingSource] = mapped_column(
        str_enum(RecordingSource, "recording_source"), default=RecordingSource.BROWSER
    )
    # Pistas: cada reanudación tras un fallo de captura abre una nueva (0, 1, 2…).
    track_count: Mapped[int] = mapped_column(Integer, default=0)
    # Suma de duraciones declaradas por el cliente; el worker la recalcula con ffprobe.
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    # Momento en que el usuario confirmó tener autorización de los participantes.
    consent_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bytes recibidos (suma de segmentos o bloques).
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # Nombre original del archivo importado (solo informativo; nunca se usa como ruta).
    source_filename: Mapped[str | None] = mapped_column(String(255))
    # Audio listo para procesar (extraído de una importación o, en la Fase 3, normalizado).
    audio_object_key: Mapped[str | None] = mapped_column(String(500))
    # Motivo estable del fallo cuando status = failed (p. ej. "malware_detected").
    error_code: Mapped[str | None] = mapped_column(String(64))
    # Aviso no bloqueante sobre la captura (p. ej. "audio_gaps": el navegador perdió audio).
    capture_warning: Mapped[str | None] = mapped_column(String(64))

    segments: Mapped[list["AudioSegment"]] = relationship(
        back_populates="recording", order_by="(AudioSegment.track, AudioSegment.seq)"
    )


class AudioSegment(Base):
    """Bloque de audio decodificable por sí solo. El id lo genera el cliente (idempotencia)."""

    __tablename__ = "audio_segments"
    __table_args__ = (
        Index("uq_audio_segments_position", "recording_id", "track", "seq", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    track: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    recording: Mapped[Recording] = relationship(back_populates="segments")

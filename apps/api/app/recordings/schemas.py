import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import RecordingSource, RecordingStatus

Language = Literal["es", "ca", "en"]


class RecordingCreate(BaseModel):
    organization_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    language: Language = "es"
    source: Literal["browser", "import"] = "browser"
    # Nombre del archivo importado (informativo).
    filename: str | None = Field(default=None, max_length=255)
    # El usuario debe confirmar que cuenta con autorización de los participantes.
    consent: Literal[True]


class TrackSummary(BaseModel):
    track: int
    segments: int
    duration_ms: int


class RecordingOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID | None
    title: str
    language: str
    status: RecordingStatus
    source: RecordingSource
    duration_ms: int
    size_bytes: int
    track_count: int
    source_filename: str | None
    # El audio está listo para transcribir (extraído de una importación).
    audio_ready: bool
    error_code: str | None
    capture_warning: str | None
    created_at: datetime
    completed_at: datetime | None


class RecordingDetail(RecordingOut):
    tracks: list[TrackSummary]


class TrackOut(BaseModel):
    track: int


class SegmentOut(BaseModel):
    id: uuid.UUID
    track: int
    seq: int
    sha256: str
    size_bytes: int
    # True si ya existía (reintento idempotente).
    duplicate: bool = False


class CompleteRequest(BaseModel):
    """Número de segmentos que el cliente ha producido en cada pista: el servidor comprueba
    que los tiene todos antes de cerrar la captura."""

    tracks: dict[int, int] = Field(min_length=1)


class MissingSegment(BaseModel):
    track: int
    seq: int


class TranscriptSegmentOut(BaseModel):
    idx: int
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str
    confidence: float | None


class TranscriptOut(BaseModel):
    id: uuid.UUID
    recording_id: uuid.UUID
    version: int
    language: str
    provider: str
    model: str
    created_at: datetime
    # Nombres dados a los hablantes: {"H1": "Laura"}. Sin entrada, "Hablante 1".
    speaker_names: dict[str, str]
    segments: list[TranscriptSegmentOut]


class ReprocessRequest(BaseModel):
    language: Language | None = None


class SpeakerNamesUpdate(BaseModel):
    """Nombre por etiqueta de hablante. Cadena vacía = quitar el nombre."""

    names: dict[str, str] = Field(max_length=50)

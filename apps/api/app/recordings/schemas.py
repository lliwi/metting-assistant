import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import TranscriptionEngine
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
    transcription_engine: str | None
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
    # Motor para esta y las siguientes transcripciones de la grabación.
    engine: TranscriptionEngine | None = None


class EnginesOut(BaseModel):
    default: TranscriptionEngine
    available: list[TranscriptionEngine]
    # El motor local identifica hablantes (pyannote configurado).
    local_speakers: bool


class SpeakerNamesUpdate(BaseModel):
    """Nombre por etiqueta de hablante. Cadena vacía = quitar el nombre."""

    names: dict[str, str] = Field(max_length=50)


class SearchMatch(BaseModel):
    """Frase de la transcripción que coincide. En `highlighted`, los términos van entre
    ⟦ y ⟧ (texto plano: el cliente los resalta sin interpretar HTML)."""

    idx: int
    start_ms: int
    highlighted: str


class RecordingListItem(RecordingOut):
    matches: list[SearchMatch] = []


class RecordingUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class SegmentUpdate(BaseModel):
    """Campos a cambiar de una frase. `speaker` null = sin hablante."""

    text: str | None = Field(default=None, min_length=1, max_length=5000)
    speaker: str | None = Field(default=None, pattern=r"^H\d{1,3}$")


class TranscriptEditOut(BaseModel):
    segment_idx: int
    field: str
    old_value: str | None
    new_value: str | None
    editor_name: str | None
    created_at: datetime

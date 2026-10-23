"""Transcripción de audio. Cada proveedor devuelve segmentos con marcas en milisegundos."""

import math
import re
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import structlog

from app.core.config import TranscriptionEngine, get_settings
from app.media.ffmpeg import split_audio

log = structlog.get_logger(__name__)


class AsrError(Exception):
    """`transient` = reintentar más tarde (red, cuota); si no, el audio no es procesable."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class AsrSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    # Etiqueta del proveedor (A, B, SPEAKER_00…); se normaliza en el pipeline.
    speaker: str | None = None


@dataclass(frozen=True)
class AsrResult:
    language: str
    segments: list[AsrSegment]
    provider: str
    model: str
    processing_ms: int


class AsrProvider(Protocol):
    def transcribe(self, path: Path, language: str | None) -> AsrResult: ...


# Frases que Whisper "inventa" sobre silencio o ruido (aprendidas de subtítulos de vídeos).
# Solo se descartan si el segmento es únicamente eso.
_HALLUCINATIONS = re.compile(
    r"^[\s¡!¿?.,]*("
    r"suscr[ií]bete( al canal)?( y dale (a )?like)?"
    r"|dale (a )?like( y suscr[ií]bete)?"
    r"|gracias por (ver|mirar)( el v[ií]deo)?"
    r"|subt[ií]tulos? (realizados? )?(por|de) .*"
    r"|.*amara\.org.*"
    r"|thanks for watching"
    r"|please subscribe"
    r"|subscribe to (my|the) channel"
    r")[\s¡!¿?.,]*$",
    re.IGNORECASE,
)
# Umbral de Whisper para "probablemente no hay voz" combinado con baja certeza del texto.
NO_SPEECH_THRESHOLD = 0.6
LOW_LOGPROB = -0.8


def is_hallucination(text: str, no_speech_prob: float | None, avg_logprob: float | None) -> bool:
    if _HALLUCINATIONS.match(text):
        return True
    return (
        no_speech_prob is not None
        and avg_logprob is not None
        and no_speech_prob > NO_SPEECH_THRESHOLD
        and avg_logprob < LOW_LOGPROB
    )


def _confidence(avg_logprob: float | None) -> float | None:
    if avg_logprob is None:
        return None
    return round(min(1.0, max(0.0, math.exp(avg_logprob))), 3)


# --- faster-whisper (local) ----------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_whisper(model: str, device: str, compute_type: str, download_root: str) -> Any:
    from faster_whisper import WhisperModel  # import pesado: solo en el worker que transcribe

    log.info("whisper_model_loading", model=model, device=device, compute_type=compute_type)
    return WhisperModel(
        model, device=device, compute_type=compute_type, download_root=download_root
    )


class FasterWhisperProvider:
    name = "faster_whisper"

    def __init__(
        self, model: str, device: str, compute_type: str, models_dir: str, beam_size: int
    ) -> None:
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.models_dir = models_dir
        self.beam_size = beam_size

    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        started = time.monotonic()
        try:
            whisper = _load_whisper(self.model, self.device, self.compute_type, self.models_dir)
            segments, info = whisper.transcribe(
                str(path),
                language=language,
                beam_size=self.beam_size,
                # El VAD descarta silencios: menos alucinaciones y más rápido.
                vad_filter=True,
                condition_on_previous_text=False,
            )
            result = [
                AsrSegment(
                    start_ms=int(s.start * 1000),
                    end_ms=int(s.end * 1000),
                    text=s.text.strip(),
                    confidence=_confidence(s.avg_logprob),
                )
                for s in segments
                if s.text.strip()
                and not is_hallucination(s.text.strip(), s.no_speech_prob, s.avg_logprob)
            ]
        except OSError as exc:  # descarga del modelo, disco
            raise AsrError(str(exc), transient=True) from exc
        except Exception as exc:
            # GPU compartida (p. ej. con Ollama): sin memoria ahora, quizá sí en unos minutos.
            out_of_memory = "out of memory" in str(exc).lower()
            if out_of_memory:
                _load_whisper.cache_clear()
            raise AsrError(str(exc), transient=out_of_memory) from exc
        return AsrResult(
            language=info.language,
            segments=result,
            provider=self.name,
            model=self.model,
            processing_ms=int((time.monotonic() - started) * 1000),
        )


# --- OpenAI (o servidor compatible) --------------------------------------------------------

# La API rechaza archivos de más de 25 MB: se trocea por tiempo con margen.
OPENAI_MAX_BYTES = 24 * 1024 * 1024
OPENAI_PIECE_SECONDS = 20 * 60


class OpenAIAsrProvider:
    name = "openai"

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        # gpt-4o-transcribe-diarize: transcribe e identifica hablantes en una llamada.
        self.diarize = "diarize" in model

    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        import openai

        started = time.monotonic()
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            pieces = (
                [(path, 0)]
                if path.stat().st_size <= OPENAI_MAX_BYTES
                else split_audio(path, Path(tmp), OPENAI_PIECE_SECONDS)
            )
            segments: list[AsrSegment] = []
            detected = language or ""
            for piece_index, (piece, offset) in enumerate(pieces, start=1):
                try:
                    with piece.open("rb") as f:
                        options: dict[str, Any] = (
                            {"response_format": "diarized_json", "chunking_strategy": "auto"}
                            if self.diarize
                            else {
                                "response_format": "verbose_json",
                                "timestamp_granularities": ["segment"],
                            }
                        )
                        response = self.client.audio.transcriptions.create(
                            model=self.model,
                            file=f,
                            language=language or openai.NOT_GIVEN,
                            **options,
                        )
                except (
                    openai.APIConnectionError,
                    openai.RateLimitError,
                    openai.InternalServerError,
                ) as exc:
                    raise AsrError(str(exc), transient=True) from exc
                except openai.APIError as exc:
                    raise AsrError(str(exc), transient=False) from exc
                detected = detected or getattr(response, "language", "") or ""
                for s in getattr(response, "segments", None) or []:
                    text = (s.text or "").strip()
                    if text and not is_hallucination(
                        text, getattr(s, "no_speech_prob", None), getattr(s, "avg_logprob", None)
                    ):
                        speaker = getattr(s, "speaker", None)
                        if speaker and len(pieces) > 1:
                            # Cada trozo se diariza por separado: "A" del trozo 1 no es
                            # necesariamente "A" del trozo 2.
                            speaker = f"{piece_index}-{speaker}"
                        segments.append(
                            AsrSegment(
                                start_ms=offset + int(s.start * 1000),
                                end_ms=offset + int(s.end * 1000),
                                text=text,
                                confidence=_confidence(getattr(s, "avg_logprob", None)),
                                speaker=speaker,
                            )
                        )
        return AsrResult(
            language=_iso_language(detected),
            segments=segments,
            provider=self.name,
            model=self.model,
            processing_ms=int((time.monotonic() - started) * 1000),
        )


_LANGUAGE_NAMES = {"spanish": "es", "catalan": "ca", "english": "en"}


def _iso_language(value: str) -> str:
    """La API de OpenAI devuelve el idioma por nombre ("spanish"); se normaliza a ISO 639-1."""
    value = value.lower()
    return _LANGUAGE_NAMES.get(value, value[:8])


def get_asr_provider(engine: TranscriptionEngine | None = None) -> AsrProvider:
    settings = get_settings()
    engine = engine or settings.default_engine
    if engine == "openai":
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=600,
            max_retries=2,
        )
        # Con la diarización desactivada se usa el modelo de transcripción sin hablantes.
        model = (
            settings.openai_transcription_model
            if settings.diarization_provider == "none"
            else settings.openai_diarize_model
        )
        return OpenAIAsrProvider(client, model)
    return FasterWhisperProvider(
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.whisper_models_dir,
        settings.whisper_beam_size,
    )

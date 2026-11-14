"""Transcripción de audio. Cada proveedor devuelve segmentos con marcas en milisegundos."""

import base64
import math
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog

from app.ai.usage import Usage
from app.core.config import TranscriptionEngine, get_settings
from app.media.ffmpeg import MediaError, cut_clip, probe, split_audio

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
    # Tokens facturados (modelos gpt-4o-transcribe*); 0 si el proveedor no los informa.
    input_tokens: int = 0
    output_tokens: int = 0


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

# La API rechaza archivos de más de 25 MB y los modelos gpt-4o limitan además la duración (unos
# 23-25 min): a 64 kbit/s, un audio de 50 min aún cabe en 24 MB. Se trocea por lo que llegue antes.
OPENAI_MAX_BYTES = 24 * 1024 * 1024
OPENAI_PIECE_SECONDS = 20 * 60
# Medido en producción: gpt-4o-transcribe-diarize tarda ~0,5 s por segundo de audio, así que un
# trozo de 20 min necesita unos 10 min. Con el límite anterior (600 s) se cortaba siempre.
OPENAI_REQUEST_TIMEOUT_S = 30 * 60
# Trozos enviados a la vez. Pocos: cada uno ocupa una petición larga de la cuota de la API.
OPENAI_PARALLEL_PIECES = 3
# Voces de referencia (known_speaker_references): la API admite 4, de 2 a 10 s cada una.
OPENAI_MAX_KNOWN_SPEAKERS = 4
OPENAI_REFERENCE_MIN_MS = 2_000
OPENAI_REFERENCE_MAX_MS = 10_000
# Margen para que el recorte no empiece ni acabe a media palabra del vecino.
OPENAI_REFERENCE_MARGIN_MS = 150


def _fits_in_one_request(path: Path) -> bool:
    return (
        path.stat().st_size <= OPENAI_MAX_BYTES
        and probe(path).duration_ms <= OPENAI_PIECE_SECONDS * 1000
    )


class OpenAIAsrProvider:
    name = "openai"

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        # gpt-4o-transcribe-diarize: transcribe e identifica hablantes en una llamada.
        self.diarize = "diarize" in model

    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            pieces = (
                [(path, 0)]
                if _fits_in_one_request(path)
                else split_audio(path, Path(tmp), OPENAI_PIECE_SECONDS)
            )
            results = self._transcribe_pieces(pieces, language)

        segments: list[AsrSegment] = []
        tokens = Usage()
        detected = language or ""
        # En el orden del audio, aunque los trozos terminen en otro orden.
        for piece in results:
            segments.extend(piece.segments)
            tokens.add(piece.input_tokens, piece.output_tokens)
            detected = detected or piece.language
        return AsrResult(
            language=_iso_language(detected),
            segments=segments,
            provider=self.name,
            model=self.model,
            processing_ms=int((time.monotonic() - started) * 1000),
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
        )

    def _transcribe_pieces(
        self, pieces: list[tuple[Path, int]], language: str | None
    ) -> list["_PieceResult"]:
        """Los trozos son independientes: en paralelo, el total es lo que tarde el más lento y
        no la suma (a ~0,5 s por segundo de audio, 4 h en serie eran 2 h)."""
        if len(pieces) == 1:
            piece, offset = pieces[0]
            return [self._transcribe_piece(piece, offset, None, language)]
        done: list[_PieceResult] = []
        known: _KnownSpeakers | None = None
        if self.diarize:
            # Cada trozo identifica a sus hablantes por su cuenta. El primero va solo y de él
            # se sacan muestras de voz: el resto las recibe como hablantes conocidos, así la
            # misma persona conserva su etiqueta en vez de aparecer como alguien nuevo.
            piece, offset = pieces[0]
            first = self._transcribe_piece(piece, offset, 1, language)
            done.append(first)
            known = _speaker_references(piece, offset, first.segments)
        rest = list(enumerate(pieces, start=1))[len(done) :]
        pool = ThreadPoolExecutor(
            max_workers=min(OPENAI_PARALLEL_PIECES, len(rest)), thread_name_prefix="asr-piece"
        )
        futures = [
            pool.submit(self._transcribe_piece, piece, offset, index, language, known)
            for index, (piece, offset) in rest
        ]
        try:
            return done + [future.result() for future in futures]
        finally:
            # Si un trozo falla, no se envían los que aún esperan turno (se reintentará todo).
            pool.shutdown(wait=True, cancel_futures=True)

    def _transcribe_piece(
        self,
        piece: Path,
        offset: int,
        piece_index: int | None,
        language: str | None,
        known: "_KnownSpeakers | None" = None,
    ) -> "_PieceResult":
        import openai

        options: dict[str, Any] = (
            {"response_format": "diarized_json", "chunking_strategy": "auto"}
            if self.diarize
            else {"response_format": "verbose_json", "timestamp_granularities": ["segment"]}
        )
        if known:
            options["known_speaker_names"] = list(known.references)
            options["known_speaker_references"] = list(known.references.values())

        def request(extra: dict[str, Any]) -> Any:
            with piece.open("rb") as f:
                return self.client.audio.transcriptions.create(
                    model=self.model, file=f, language=language or openai.NOT_GIVEN, **extra
                )

        try:
            try:
                response = request(options)
            except openai.BadRequestError as exc:
                if not known:
                    raise
                # Si la API rechaza las muestras de voz, el trozo se transcribe sin ellas:
                # peor reparto de hablantes, pero no se pierde la transcripción.
                log.warning("asr_speaker_references_rejected", piece=piece_index, error=str(exc))
                known = None
                options.pop("known_speaker_names")
                options.pop("known_speaker_references")
                response = request(options)
        except (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise AsrError(str(exc), transient=True) from exc
        except openai.APIError as exc:
            raise AsrError(str(exc), transient=False) from exc

        result = _PieceResult(language=getattr(response, "language", "") or "")
        usage = getattr(response, "usage", None)
        if usage is not None and getattr(usage, "type", "tokens") == "tokens":
            result.input_tokens = getattr(usage, "input_tokens", 0) or 0
            result.output_tokens = getattr(usage, "output_tokens", 0) or 0
        for s in getattr(response, "segments", None) or []:
            text = (s.text or "").strip()
            if not text or is_hallucination(
                text, getattr(s, "no_speech_prob", None), getattr(s, "avg_logprob", None)
            ):
                continue
            speaker = getattr(s, "speaker", None)
            if speaker and known and speaker in known.labels:
                speaker = known.labels[speaker]  # reconocido por su voz: etiqueta del trozo 1
            elif speaker and piece_index is not None:
                # Cada trozo se diariza por separado: "A" del trozo 1 no es necesariamente
                # "A" del trozo 2.
                speaker = f"{piece_index}-{speaker}"
            result.segments.append(
                AsrSegment(
                    start_ms=offset + int(s.start * 1000),
                    end_ms=offset + int(s.end * 1000),
                    text=text,
                    confidence=_confidence(getattr(s, "avg_logprob", None)),
                    speaker=speaker,
                )
            )
        return result


@dataclass(frozen=True)
class _KnownSpeakers:
    # nombre enviado a la API → muestra de voz (data URL) / etiqueta en la transcripción.
    references: dict[str, str]
    labels: dict[str, str]


def _speaker_references(
    piece: Path, offset: int, segments: list[AsrSegment]
) -> _KnownSpeakers | None:
    """Muestra de voz de los hablantes del primer trozo: de cada uno, su intervención más
    larga; si hay más de los que admite la API, los que más hablan."""
    talk: dict[str, int] = {}
    longest: dict[str, AsrSegment] = {}
    for seg in segments:
        if not seg.speaker:
            continue
        length = seg.end_ms - seg.start_ms
        talk[seg.speaker] = talk.get(seg.speaker, 0) + length
        best = longest.get(seg.speaker)
        if best is None or length > best.end_ms - best.start_ms:
            longest[seg.speaker] = seg
    references: dict[str, str] = {}
    labels: dict[str, str] = {}
    for label in sorted(talk, key=talk.__getitem__, reverse=True):
        if len(references) == OPENAI_MAX_KNOWN_SPEAKERS:
            break
        seg = longest[label]
        start = seg.start_ms + OPENAI_REFERENCE_MARGIN_MS
        length = min(seg.end_ms - OPENAI_REFERENCE_MARGIN_MS - start, OPENAI_REFERENCE_MAX_MS)
        if length < OPENAI_REFERENCE_MIN_MS:
            continue  # demasiado breve para reconocer la voz
        clip = piece.with_name(f"reference-{len(references)}.wav")
        try:
            cut_clip(piece, clip, start - offset, length)
            data = base64.b64encode(clip.read_bytes()).decode()
        except (MediaError, OSError) as exc:
            log.warning("asr_speaker_reference_failed", speaker=label, error=str(exc)[:300])
            continue
        name = f"speaker{len(references) + 1}"
        references[name] = f"data:audio/wav;base64,{data}"
        labels[name] = label
    return _KnownSpeakers(references, labels) if references else None


@dataclass
class _PieceResult:
    language: str
    segments: list[AsrSegment] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


_LANGUAGE_NAMES = {
    "spanish": "es", "catalan": "ca", "english": "en", "italian": "it",
    "spa": "es", "cat": "ca", "eng": "en", "ita": "it",
}  # fmt: skip


def _iso_language(value: str) -> str:
    """OpenAI devuelve el idioma por nombre ("spanish") y ElevenLabs en ISO 639-3 ("spa"); se
    normaliza a ISO 639-1."""
    value = value.lower()
    return _LANGUAGE_NAMES.get(value, value[:8])


# --- ElevenLabs (Scribe) -------------------------------------------------------------------

# La grabación entera va en una petición (hasta 5 GB / 15 h): los hablantes se identifican
# sobre todo el audio, sin trozos que reconciliar. La respuesta es síncrona.
ELEVENLABS_REQUEST_TIMEOUT_S = 60 * 60
# La respuesta viene palabra a palabra: se agrupan en frases. Una pausa así, o esta duración,
# cierran la frase aunque no haya signo de puntuación.
ELEVENLABS_PAUSE_MS = 1_500
ELEVENLABS_SEGMENT_MAX_MS = 30_000
_SENTENCE_END = (".", "?", "!", "…")


class ElevenLabsAsrProvider:
    name = "elevenlabs"

    def __init__(self, http: httpx.Client, model: str, *, diarize: bool) -> None:
        self.http = http
        self.model = model
        self.diarize = diarize

    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        started = time.monotonic()
        data = {
            "model_id": self.model,
            "diarize": "true" if self.diarize else "false",
            "timestamps_granularity": "word",
            # Sin "(risas)" ni "(música)" en la transcripción.
            "tag_audio_events": "false",
        }
        if language:
            data["language_code"] = language
        try:
            with path.open("rb") as f:
                response = self.http.post(
                    "/v1/speech-to-text", data=data, files={"file": (path.name, f)}
                )
        except httpx.TransportError as exc:
            raise AsrError(f"ElevenLabs no responde: {exc}", transient=True) from exc
        if response.status_code >= 400:
            raise AsrError(
                f"ElevenLabs {response.status_code}: {response.text[:300]}",
                transient=response.status_code in (408, 429) or response.status_code >= 500,
            )
        body = response.json()
        return AsrResult(
            language=_iso_language(language or body.get("language_code") or ""),
            segments=_segments_from_words(body.get("words") or []),
            provider=self.name,
            model=self.model,
            processing_ms=int((time.monotonic() - started) * 1000),
        )


def _segments_from_words(words: list[dict[str, Any]]) -> list[AsrSegment]:
    """Palabras con su hablante → frases. Se corta al cambiar de hablante, al acabar la frase,
    tras una pausa o al alcanzar la duración máxima."""
    segments: list[AsrSegment] = []
    current: list[dict[str, Any]] = []

    def close() -> None:
        spoken = [w for w in current if w.get("type") == "word"]
        text = "".join(str(w.get("text") or "") for w in current).strip()
        if spoken and text:
            logprobs = [w["logprob"] for w in spoken if isinstance(w.get("logprob"), int | float)]
            segments.append(
                AsrSegment(
                    start_ms=int(spoken[0]["start"] * 1000),
                    end_ms=int(spoken[-1]["end"] * 1000),
                    text=text,
                    confidence=_confidence(sum(logprobs) / len(logprobs)) if logprobs else None,
                    speaker=spoken[0].get("speaker_id"),
                )
            )
        current.clear()

    last: dict[str, Any] | None = None  # última palabra de la frase en curso
    for word in words:
        kind = word.get("type")
        if kind == "spacing":
            # Un solo espacio aunque entre dos palabras hubiera un evento de audio descartado.
            if current and current[-1].get("type") != "spacing":
                current.append(word)
            continue
        if kind != "word" or word.get("start") is None or word.get("end") is None:
            continue
        if last is not None and (
            word.get("speaker_id") != last.get("speaker_id")
            or str(last.get("text") or "").rstrip().endswith(_SENTENCE_END)
            or (word["start"] - last["end"]) * 1000 > ELEVENLABS_PAUSE_MS
            or (word["end"] - current[0]["start"]) * 1000 > ELEVENLABS_SEGMENT_MAX_MS
        ):
            close()
        current.append(word)
        last = word
    close()
    return segments


def get_asr_provider(engine: TranscriptionEngine | None = None) -> AsrProvider:
    settings = get_settings()
    engine = engine or settings.default_engine
    if engine == "elevenlabs":
        http = httpx.Client(
            base_url=settings.elevenlabs_base_url.rstrip("/"),
            headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
            timeout=httpx.Timeout(ELEVENLABS_REQUEST_TIMEOUT_S, connect=30),
        )
        return ElevenLabsAsrProvider(
            http, settings.elevenlabs_model, diarize=settings.diarization_provider != "none"
        )
    if engine == "openai":
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=OPENAI_REQUEST_TIMEOUT_S,
            # Un reintento: la tarea ya reintenta los fallos transitorios con espera creciente.
            max_retries=1,
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

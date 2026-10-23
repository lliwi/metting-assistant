"""Diarización local con pyannote (opcional; requiere HF_TOKEN y la imagen con GPU)."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import structlog

from app.ai.asr import AsrError
from app.ai.speakers import SpeakerTurn
from app.core.config import TranscriptionEngine, get_settings

log = structlog.get_logger(__name__)


class Diarizer(Protocol):
    def diarize(self, path: Path) -> list[SpeakerTurn]: ...


@lru_cache(maxsize=1)
def _load_pipeline(model: str, token: str, device: str) -> Any:
    import torch
    from pyannote.audio import Pipeline

    log.info("pyannote_loading", model=model, device=device)
    try:
        pipeline = Pipeline.from_pretrained(model, token=token)
    except TypeError:  # pyannote.audio < 4
        pipeline = Pipeline.from_pretrained(model, use_auth_token=token)  # type: ignore[call-arg]
    if pipeline is None:
        raise AsrError(f"No se pudo cargar {model}: ¿token sin acceso al modelo?", transient=False)
    if device == "cuda" and torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
    return pipeline


class PyannoteDiarizer:
    def __init__(self, model: str, token: str, device: str) -> None:
        self.model = model
        self.token = token
        self.device = device

    def diarize(self, path: Path) -> list[SpeakerTurn]:
        import torch
        from faster_whisper.audio import decode_audio

        try:
            pipeline = _load_pipeline(self.model, self.token, self.device)
            # Se pasa la forma de onda ya decodificada: evita depender del decodificador de
            # pyannote para Opus/WebM.
            waveform = torch.from_numpy(decode_audio(str(path), sampling_rate=16_000))[None, :]
            output = pipeline({"waveform": waveform, "sample_rate": 16_000})
        except AsrError:
            raise
        except Exception as exc:
            out_of_memory = "out of memory" in str(exc).lower()
            if out_of_memory:
                _load_pipeline.cache_clear()
            raise AsrError(f"Diarización: {exc}", transient=out_of_memory) from exc
        # pyannote 4 devuelve un objeto con la anotación; pyannote 3, la anotación directamente.
        annotation = getattr(output, "exclusive_speaker_diarization", None) or getattr(
            output, "speaker_diarization", output
        )
        return [
            SpeakerTurn(int(turn.start * 1000), int(turn.end * 1000), str(speaker))
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        ]


def get_diarizer(engine: TranscriptionEngine | None = None) -> Diarizer | None:
    """Solo el motor local diariza después del ASR; con OpenAI viene en la transcripción."""
    settings = get_settings()
    if (engine or settings.default_engine) != "local" or not settings.local_diarization:
        return None
    return PyannoteDiarizer(
        settings.pyannote_model, settings.hf_token.get_secret_value(), settings.whisper_device
    )

"""Hablantes: asignación por solapamiento, etiquetas normalizadas y unión de turnos."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.ai.asr import AsrSegment


@dataclass(frozen=True)
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker: str


def assign_speakers(
    segments: Sequence[AsrSegment], turns: Sequence[SpeakerTurn]
) -> list[AsrSegment]:
    """A cada segmento de texto, el hablante con el que más se solapa en el tiempo."""
    result = []
    for seg in segments:
        overlap: dict[str, int] = {}
        for turn in turns:
            shared = min(seg.end_ms, turn.end_ms) - max(seg.start_ms, turn.start_ms)
            if shared > 0:
                overlap[turn.speaker] = overlap.get(turn.speaker, 0) + shared
        speaker = max(overlap, key=overlap.__getitem__) if overlap else None
        result.append(replace(seg, speaker=speaker))
    return result


def normalize_speakers(segments: Sequence[AsrSegment]) -> list[AsrSegment]:
    """Etiquetas del proveedor (A, SPEAKER_00…) → H1, H2… por orden de aparición."""
    labels: dict[str, str] = {}
    result = []
    for seg in segments:
        if seg.speaker is None:
            result.append(seg)
            continue
        label = labels.setdefault(seg.speaker, f"H{len(labels) + 1}")
        result.append(replace(seg, speaker=label))
    return result


def merge_turns(
    segments: Sequence[AsrSegment], *, max_gap_ms: int = 1_500, max_len_ms: int = 30_000
) -> list[AsrSegment]:
    """Une fragmentos consecutivos del mismo hablante en un turno (párrafo) legible."""
    merged: list[AsrSegment] = []
    for seg in segments:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and seg.speaker is not None
            and seg.speaker == prev.speaker
            and seg.start_ms - prev.end_ms <= max_gap_ms
            and seg.end_ms - prev.start_ms <= max_len_ms
        ):
            confidences = [c for c in (prev.confidence, seg.confidence) if c is not None]
            merged[-1] = replace(
                prev,
                end_ms=seg.end_ms,
                text=f"{prev.text} {seg.text}",
                confidence=min(confidences) if confidences else None,
            )
        else:
            merged.append(seg)
    return merged


def prepare_speakers(segments: Sequence[AsrSegment]) -> list[AsrSegment]:
    if not any(s.speaker for s in segments):
        return list(segments)
    return merge_turns(normalize_speakers(segments))

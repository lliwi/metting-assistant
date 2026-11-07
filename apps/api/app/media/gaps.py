"""Huecos de audio: tramos de una captura del navegador que no llegaron al servidor o que el
navegador no llegó a capturar.

Ningún modelo puede recuperar lo que no se grabó: se marcan en la transcripción, en las
exportaciones y en el texto que recibe el LLM, para que nadie lea un salto como continuidad (ni
el modelo lo rellene inventando).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

GapKind = Literal["missing", "interruption", "dropped"]

# Duración de cada segmento que graba el navegador (SEGMENT_MS en la web): para estimar cuánto
# audio faltaba cuando se pierden segmentos enteros.
CLIENT_SEGMENT_MS = 30_000
# Pérdida de muestras dentro de un segmento: mismos umbrales que el aviso general de captura.
MIN_CAPTURED_RATIO = 0.85
MIN_MISSING_MS = 2_000


@dataclass(frozen=True)
class AudioGap:
    # Posición en el audio unido (el que se reproduce y se transcribe).
    start_ms: int
    kind: GapKind
    # Audio perdido estimado, si se conoce.
    lost_ms: int | None = None
    # Segmentos que no llegaron (solo "missing").
    segments: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "kind": self.kind,
            "lost_ms": self.lost_ms,
            "segments": self.segments,
        }


def located_gaps(entries: Iterable[dict[str, Any]] | None) -> list[AudioGap]:
    """Huecos ya situados en el audio. Antes de unirlo, la columna guarda solo qué segmentos
    faltaban (sin `start_ms`): esos aún no tienen posición."""
    gaps = []
    for entry in entries or []:
        if isinstance(entry, dict) and isinstance(entry.get("start_ms"), int):
            gaps.append(
                AudioGap(
                    start_ms=entry["start_ms"],
                    kind=entry.get("kind", "missing"),
                    lost_ms=entry.get("lost_ms"),
                    segments=entry.get("segments"),
                )
            )
    return sorted(gaps, key=lambda g: g.start_ms)


def missing_positions(entries: Iterable[dict[str, Any]] | None) -> set[tuple[int, int]]:
    """(pista, número) de los segmentos que faltaban al cerrar la captura."""
    return {
        (entry["track"], entry["seq"])
        for entry in entries or []
        if isinstance(entry, dict) and "start_ms" not in entry and "track" in entry
    }


def dropped_ms(captured_ms: int, declared_ms: int) -> int:
    """Audio que el navegador dejó de capturar dentro de un segmento (0 si no hay pérdida)."""
    missing = declared_ms - captured_ms
    if (
        declared_ms > 0
        and missing > MIN_MISSING_MS
        and captured_ms < declared_ms * MIN_CAPTURED_RATIO
    ):
        return missing
    return 0


@dataclass(frozen=True)
class Piece:
    """Un segmento de la captura en su orden: presente (con su audio medido) o perdido."""

    track: int
    seq: int
    captured_ms: int | None  # None: el segmento no llegó
    declared_ms: int = 0


def locate(pieces: Sequence[Piece], timeline_ms: int) -> list[AudioGap]:
    """Sitúa los huecos en el audio unido.

    Las posiciones se calculan sumando el audio de cada segmento y se ajustan a la duración real
    del audio unido (al unir, FFmpeg rellena con silencio los saltos de tiempo): son aproximadas,
    de unos segundos.
    """
    gaps: list[AudioGap] = []
    pos = 0
    previous_track: int | None = None
    missing_from: int | None = None
    missing_count = 0

    def close_missing() -> None:
        nonlocal missing_from, missing_count
        if missing_from is not None:
            gaps.append(
                AudioGap(
                    missing_from,
                    "missing",
                    lost_ms=missing_count * CLIENT_SEGMENT_MS,
                    segments=missing_count,
                )
            )
        missing_from, missing_count = None, 0

    for piece in sorted(pieces, key=lambda p: (p.track, p.seq)):
        if previous_track is not None and piece.track != previous_track:
            close_missing()
            if pos > 0:
                gaps.append(AudioGap(pos, "interruption"))
        previous_track = piece.track
        if piece.captured_ms is None:
            if missing_from is None:
                missing_from = pos
            missing_count += 1
            continue
        close_missing()
        lost = dropped_ms(piece.captured_ms, piece.declared_ms)
        if lost:
            last = gaps[-1] if gaps else None
            if (
                last is not None
                and last.kind == "dropped"
                and last.start_ms + (last.lost_ms or 0) >= pos - 1
            ):
                # Segmentos seguidos con pérdidas (pantalla apagada un rato): un solo aviso.
                gaps[-1] = AudioGap(last.start_ms, "dropped", lost_ms=(last.lost_ms or 0) + lost)
            else:
                gaps.append(AudioGap(pos, "dropped", lost_ms=lost))
        pos += piece.captured_ms
    close_missing()

    if pos <= 0:
        return gaps
    scale = timeline_ms / pos if timeline_ms > 0 else 1.0
    return [
        AudioGap(min(round(g.start_ms * scale), max(timeline_ms, 0)), g.kind, g.lost_ms, g.segments)
        for g in gaps
    ]


# --- Textos (exportaciones y LLM) ------------------------------------------------------------

_TEXTS = {
    "es": {
        "missing": (
            "Audio perdido: faltan {n} fragmento(s), unos {s} s, que no llegaron al servidor."
        ),
        "interruption": "La grabación se interrumpió aquí y se reanudó más tarde.",
        "dropped": "El navegador perdió unos {s} s de audio en este tramo.",
    },
    "ca": {
        "missing": (
            "Àudio perdut: falten {n} fragment(s), uns {s} s, que no van arribar al servidor."
        ),
        "interruption": "L'enregistrament es va interrompre aquí i es va reprendre més tard.",
        "dropped": "El navegador va perdre uns {s} s d'àudio en aquest tram.",
    },
}


def describe(gap: AudioGap, locale: str = "es") -> str:
    texts = _TEXTS.get(locale, _TEXTS["es"])
    seconds = round((gap.lost_ms or 0) / 1000)
    return texts[gap.kind].format(n=gap.segments or 0, s=seconds)

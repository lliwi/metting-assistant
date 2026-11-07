"""Unificar hablantes que el diarizador separó en varias etiquetas.

Con OpenAI, un audio largo se transcribe por trozos de 20 min y cada trozo identifica a sus
hablantes por su cuenta: la misma persona aparece como H1 en el primero y H4 en el segundo.

El LLM solo sugiere; la persona confirma. Y solo puede proponer etiquetas que nunca hablan a la
vez en el tiempo (las de un mismo trozo se solapan: el diarizador ya las distinguió).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Protocol

from app.ai.documents import LANGUAGE_NAMES, _clock
from app.ai.llm import LlmProvider

# Frases de muestra por etiqueta: bastan para reconocer a alguien y acotan el coste.
SAMPLES_PER_SPEAKER = 6
SAMPLE_CHARS = 240


class SegmentLike(Protocol):
    @property
    def start_ms(self) -> int: ...
    @property
    def end_ms(self) -> int: ...
    @property
    def speaker(self) -> str | None: ...
    @property
    def text(self) -> str: ...


@dataclass(frozen=True)
class Span:
    first_ms: int
    last_ms: int
    turns: int


@dataclass(frozen=True)
class Suggestion:
    labels: list[str]
    name: str | None
    reason: str


def spans(segments: Sequence[SegmentLike]) -> dict[str, Span]:
    """Tramo de la grabación en que interviene cada etiqueta."""
    out: dict[str, Span] = {}
    for seg in segments:
        if not seg.speaker:
            continue
        prev = out.get(seg.speaker)
        out[seg.speaker] = (
            Span(seg.start_ms, seg.end_ms, 1)
            if prev is None
            else Span(
                min(prev.first_ms, seg.start_ms), max(prev.last_ms, seg.end_ms), prev.turns + 1
            )
        )
    return out


def disjoint(a: Span, b: Span) -> bool:
    return a.last_ms <= b.first_ms or b.last_ms <= a.first_ms


def mergeable(labels: Sequence[str], found: dict[str, Span]) -> bool:
    return (
        len(labels) >= 2
        and all(label in found for label in labels)
        and all(disjoint(found[a], found[b]) for a, b in combinations(labels, 2))
    )


def has_candidates(found: dict[str, Span]) -> bool:
    return any(disjoint(found[a], found[b]) for a, b in combinations(found, 2))


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grupos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "etiquetas": {"type": "array", "items": {"type": "string"}},
                    "nombre": {"type": ["string", "null"]},
                    "motivo": {"type": "string"},
                },
                "required": ["etiquetas", "nombre", "motivo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["grupos"],
    "additionalProperties": False,
}

SYSTEM = """Analizas la transcripción automática de una reunión. Se transcribió por trozos y en
cada trozo se identificó a los hablantes por separado, así que una misma persona puede aparecer
con varias etiquetas (H1 en el primer trozo, H4 en el segundo).

Recibes cada etiqueta con el tramo de tiempo en que habla y algunas de sus frases. Agrupa las
etiquetas que sean la misma persona.

Reglas:
- Solo pueden ser la misma persona etiquetas cuyos tramos no se solapan.
- Básate en pistas concretas: cómo se dirigen a ella o se presenta ("Laura, ¿tú qué opinas?",
  "soy Pau"), su papel en la reunión, los temas que lleva o su forma de hablar.
- Si no hay pistas claras, no agrupes: es mejor no proponer nada que unir a dos personas
  distintas.
- "nombre": el nombre de la persona si aparece en el texto; si no, null. No lo inventes.
- "motivo": una frase breve con la pista en que te basas, en {language}.
- Cada etiqueta, como mucho en un grupo. Un grupo tiene al menos dos etiquetas."""


def build_prompt(
    segments: Sequence[SegmentLike], found: dict[str, Span], names: dict[str, str]
) -> str:
    by_label: dict[str, list[SegmentLike]] = {}
    for seg in segments:
        if seg.speaker and seg.text.strip():
            by_label.setdefault(seg.speaker, []).append(seg)
    blocks = []
    for label in sorted(found, key=lambda lb: (found[lb].first_ms, lb)):
        said = by_label.get(label, [])
        # Las primeras (suelen incluir saludos y presentaciones) y las más largas.
        picked = said[:3] + sorted(said[3:], key=lambda s: len(s.text), reverse=True)[:3]
        picked = sorted(picked, key=lambda s: s.start_ms)[:SAMPLES_PER_SPEAKER]
        span = found[label]
        known = f" (nombre asignado: {names[label]})" if names.get(label) else ""
        lines = [
            f"{label}{known}: habla de {_clock(span.first_ms)} a {_clock(span.last_ms)},"
            f" {span.turns} intervenciones"
        ]
        lines += [f'  ({_clock(s.start_ms)}) "{s.text.strip()[:SAMPLE_CHARS]}"' for s in picked]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def suggest(
    llm: LlmProvider,
    segments: Sequence[SegmentLike],
    names: dict[str, str],
    language: str,
) -> list[Suggestion]:
    """Grupos de etiquetas que probablemente son la misma persona. Lo que el modelo proponga
    fuera de las reglas (etiquetas inexistentes, que se solapan o repetidas) se descarta."""
    found = spans(segments)
    if not has_candidates(found):
        return []  # nada que unificar: no se llama al modelo
    system = SYSTEM.format(language=LANGUAGE_NAMES.get(language, language))
    result = llm.complete_json(system, build_prompt(segments, found, names), SCHEMA)

    used: set[str] = set()
    out = []
    for group in result.get("grupos") or []:
        if not isinstance(group, dict):
            continue
        labels = sorted(
            {str(lb).strip() for lb in group.get("etiquetas") or []},
            key=lambda lb: (found[lb].first_ms if lb in found else 0, lb),
        )
        if not mergeable(labels, found) or used & set(labels):
            continue
        used |= set(labels)
        name = group.get("nombre")
        name = str(name).strip()[:100] if isinstance(name, str) and name.strip() else None
        out.append(Suggestion(labels, name, str(group.get("motivo") or "").strip()[:300]))
    return out

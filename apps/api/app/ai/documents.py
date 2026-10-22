"""Resumen y acta de reunión generados con un LLM a partir de la transcripción.

- La transcripción se presenta numerada: `[12] (03:15) Laura: texto`. El modelo cita esos
  números; el backend descarta las citas que no existen y añade su marca de tiempo, para
  enlazar cada punto con el momento del audio.
- Si la transcripción no cabe en el contexto del modelo, se extraen notas por bloques (map)
  y el documento final se redacta a partir de ellas (reduce), conservando las citas.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.ai.llm import LlmProvider

# Cambiar los prompts o los esquemas exige subir la versión (trazabilidad).
PROMPT_VERSION = "2026-10-22.2"

LANGUAGE_NAMES = {"es": "español", "ca": "catalán", "en": "inglés"}

# Estimación conservadora de tokens por carácter en es/ca/en.
CHARS_PER_TOKEN = 3.0


@dataclass(frozen=True)
class Line:
    idx: int
    start_ms: int
    speaker: str | None
    text: str


def _clock(ms: int) -> str:
    s = ms // 1000
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def speaker_label(speaker: str | None, names: dict[str, str]) -> str:
    if not speaker:
        return ""
    if speaker in names:
        return names[speaker]
    return f"Hablante {speaker.removeprefix('H')}"


def format_lines(lines: Sequence[Line], names: dict[str, str]) -> str:
    out = []
    for line in lines:
        who = speaker_label(line.speaker, names)
        out.append(f"[{line.idx}] ({_clock(line.start_ms)}) {who + ': ' if who else ''}{line.text}")
    return "\n".join(out)


# --- Esquemas (compatibles con Ollama y con json_schema estricto de OpenAI) ------------------

_REFS = {"type": "array", "items": {"type": "integer"}}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _items(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": _obj(properties)}


SUMMARY_SCHEMA = _obj(
    {
        "resumen": {"type": "string"},
        # minItems: sin él, los modelos pequeños a veces devuelven la lista vacía.
        "puntos_clave": {
            **_items({"texto": {"type": "string"}, "segmentos": _REFS}),
            "minItems": 1,
        },
    }
)

MINUTES_SCHEMA = _obj(
    {
        "titulo": {"type": "string"},
        "asistentes": {"type": "array", "items": {"type": "string"}},
        "temas": _items(
            {"titulo": {"type": "string"}, "resumen": {"type": "string"}, "segmentos": _REFS}
        ),
        "decisiones": _items({"texto": {"type": "string"}, "segmentos": _REFS}),
        "tareas": _items(
            {
                "descripcion": {"type": "string"},
                "responsable": {"type": ["string", "null"]},
                "fecha_limite": {"type": ["string", "null"]},
                "segmentos": _REFS,
            }
        ),
        "preguntas_abiertas": _items({"texto": {"type": "string"}, "segmentos": _REFS}),
    }
)

NOTES_SCHEMA = _obj(
    {
        "notas": _items({"texto": {"type": "string"}, "segmentos": _REFS}),
        "decisiones": _items({"texto": {"type": "string"}, "segmentos": _REFS}),
        "tareas": _items(
            {
                "descripcion": {"type": "string"},
                "responsable": {"type": ["string", "null"]},
                "fecha_limite": {"type": ["string", "null"]},
                "segmentos": _REFS,
            }
        ),
    }
)

SCHEMAS = {"summary": SUMMARY_SCHEMA, "minutes": MINUTES_SCHEMA}


# --- Prompts -------------------------------------------------------------------------------

_COMMON = """Trabajas con la transcripción automática de una reunión. Cada línea empieza por su
número entre corchetes, la marca de tiempo y, si se conoce, quién habla.

Reglas:
- Usa únicamente información presente en el texto. No inventes datos, nombres ni fechas.
- En "segmentos" pon los números de línea de los que sale cada punto (al menos uno).
- La transcripción puede contener errores de reconocimiento: interprétalos con prudencia.
- Escribe en {language}."""

_SUMMARY = (
    _COMMON
    + """

Tarea: resumen ejecutivo.
- "resumen": 1 a 3 párrafos con el propósito de la reunión y sus conclusiones.
- "puntos_clave": de 3 a 8 puntos, los más importantes, en frases breves."""
)

_MINUTES = (
    _COMMON
    + """

Tarea: acta de la reunión.
- "titulo": título breve y descriptivo.
- "asistentes": personas que intervienen. Usa el nombre si aparece en la transcripción o en la
  etiqueta del hablante; si no, la etiqueta ("Hablante 1").
- "temas": los asuntos tratados, en orden, cada uno con un resumen de lo dicho.
- "decisiones": solo lo que se acordó explícitamente.
- "tareas": compromisos concretos. "responsable" y "fecha_limite" solo si se mencionan; si
  no, null. Escribe la fecha tal como se dijo ("el viernes").
- "preguntas_abiertas": cuestiones que quedaron sin resolver.
Las listas pueden quedar vacías si no hay contenido de ese tipo."""
)

_NOTES = (
    _COMMON
    + """

Tarea: este es un fragmento de una reunión larga. Extrae notas para redactar después el
resumen y el acta: hechos y argumentos relevantes, decisiones y tareas, con sus segmentos."""
)

_FROM_NOTES = """
Esta vez no recibes la transcripción completa, sino notas extraídas de ella por fragmentos.
Cada nota conserva los números de línea originales entre corchetes: úsalos en "segmentos"."""

PROMPTS = {"summary": _SUMMARY, "minutes": _MINUTES}


# --- Generación ----------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedDocument:
    content: dict[str, Any]
    provider: str
    model: str
    chunks: int


def _chunk(lines: Sequence[Line], names: dict[str, str], budget_chars: int) -> list[list[Line]]:
    chunks: list[list[Line]] = [[]]
    size = 0
    for line in lines:
        cost = len(format_lines([line], names)) + 1
        if chunks[-1] and size + cost > budget_chars:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += cost
    return chunks


def _format_notes(notes: Sequence[dict[str, Any]]) -> str:
    out = []
    for part in notes:
        for key, label in (("notas", "Nota"), ("decisiones", "Decisión"), ("tareas", "Tarea")):
            for item in part.get(key, []):
                text = item.get("texto") or item.get("descripcion") or ""
                refs = ",".join(str(r) for r in item.get("segmentos", []))
                extra = ""
                if key == "tareas":
                    extra = (
                        f" (responsable: {item.get('responsable') or '—'};"
                        f" fecha: {item.get('fecha_limite') or '—'})"
                    )
                out.append(f"[{refs}] {label}: {text}{extra}")
    return "\n".join(out)


def generate(
    llm: LlmProvider,
    kind: str,
    lines: Sequence[Line],
    names: dict[str, str],
    language: str,
    *,
    context_tokens: int,
    progress: Callable[[str], None] | None = None,
) -> GeneratedDocument:
    lang = LANGUAGE_NAMES.get(language, language)
    system = PROMPTS[kind].format(language=lang)
    # Reserva para instrucciones y respuesta; el resto, para el texto de la reunión.
    budget_chars = int(max(2_000, context_tokens - 4_000) * CHARS_PER_TOKEN)
    transcript = format_lines(lines, names)

    if len(transcript) <= budget_chars:
        content = llm.complete_json(system, transcript, SCHEMAS[kind])
        return GeneratedDocument(content, llm.name, llm.model, chunks=1)

    chunks = _chunk(lines, names, budget_chars)
    notes_system = _NOTES.format(language=lang)
    notes = []
    for i, chunk in enumerate(chunks, start=1):
        if progress:
            progress(f"notes {i}/{len(chunks)}")
        notes.append(llm.complete_json(notes_system, format_lines(chunk, names), NOTES_SCHEMA))
    content = llm.complete_json(system + _FROM_NOTES, _format_notes(notes), SCHEMAS[kind])
    return GeneratedDocument(content, llm.name, llm.model, chunks=len(chunks))


def attach_references(content: Any, starts: dict[int, int]) -> Any:
    """Sustituye cada lista "segmentos" por citas válidas con su marca de tiempo:
    [{"idx": 12, "start_ms": 195000}]. Descarta números que no existen en la transcripción."""
    if isinstance(content, dict):
        result = {}
        for key, value in content.items():
            if key == "segmentos" and isinstance(value, list):
                refs = sorted({v for v in value if isinstance(v, int) and v in starts})
                result[key] = [{"idx": r, "start_ms": starts[r]} for r in refs]
            else:
                result[key] = attach_references(value, starts)
        return result
    if isinstance(content, list):
        return [attach_references(v, starts) for v in content]
    return content


_GENERIC_SPEAKER = re.compile(r"^hablante\s*(\d+)$", re.IGNORECASE)


def clean_attendees(content: dict[str, Any], speakers: set[str]) -> dict[str, Any]:
    """Quita de "asistentes" las etiquetas genéricas ("Hablante 2") que no corresponden a
    ningún hablante detectado: los modelos pequeños las inventan si no hay diarización."""
    attendees = content.get("asistentes")
    if not isinstance(attendees, list):
        return content
    kept = []
    for name in attendees:
        match = _GENERIC_SPEAKER.match(str(name).strip())
        if match and f"H{match.group(1)}" not in speakers:
            continue
        if name not in kept:
            kept.append(name)
    return {**content, "asistentes": kept}

"""Validación de los documentos de IA editados a mano (misma forma que genera el modelo)."""

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.models import AiOutputKind


def _ref_index(value: Any) -> Any:
    # Las citas llegan como {"idx": 3, "start_ms": ...} (tal como se leyeron) o como 3.
    return value.get("idx") if isinstance(value, dict) else value


Refs = list[Annotated[int, BeforeValidator(_ref_index)]]
Text = Annotated[str, Field(max_length=4000)]
Short = Annotated[str, Field(max_length=300)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Point(_Strict):
    texto: Text
    segmentos: Refs = []


class Topic(_Strict):
    titulo: Short
    resumen: Text
    segmentos: Refs = []


class Task(_Strict):
    descripcion: Text
    responsable: Short | None = None
    fecha_limite: Short | None = None
    segmentos: Refs = []


class SummaryContent(_Strict):
    resumen: Annotated[str, Field(max_length=20_000)]
    puntos_clave: Annotated[list[Point], Field(max_length=50)]


class MinutesContent(_Strict):
    titulo: Short
    asistentes: Annotated[list[Short], Field(max_length=100)]
    temas: Annotated[list[Topic], Field(max_length=100)]
    decisiones: Annotated[list[Point], Field(max_length=100)]
    tareas: Annotated[list[Task], Field(max_length=100)]
    preguntas_abiertas: Annotated[list[Point], Field(max_length=100)]


class Section(_Strict):
    titulo: Short
    contenido: Annotated[str, Field(max_length=20_000)]
    segmentos: Refs = []


class Concept(_Strict):
    termino: Short
    definicion: Text
    segmentos: Refs = []


class Homework(_Strict):
    descripcion: Text
    fecha_limite: Short | None = None
    segmentos: Refs = []


class ClassNotesContent(_Strict):
    titulo: Short
    introduccion: Annotated[str, Field(max_length=20_000)]
    secciones: Annotated[list[Section], Field(max_length=100)]
    conceptos: Annotated[list[Concept], Field(max_length=200)]
    ejemplos: Annotated[list[Point], Field(max_length=100)]
    para_repasar: Annotated[list[Point], Field(max_length=100)]
    deberes: Annotated[list[Homework], Field(max_length=100)]


MODELS: dict[AiOutputKind, type[_Strict]] = {
    AiOutputKind.SUMMARY: SummaryContent,
    AiOutputKind.MINUTES: MinutesContent,
    AiOutputKind.CLASS_NOTES: ClassNotesContent,
}


def validate_content(kind: AiOutputKind, content: dict[str, Any]) -> dict[str, Any]:
    """Valida y normaliza; las citas quedan como índices (luego se les añade el tiempo)."""
    return MODELS[kind].model_validate(content).model_dump()

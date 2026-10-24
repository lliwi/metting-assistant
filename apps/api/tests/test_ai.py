"""Resumen y acta con LLM, hablantes y sus endpoints."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.ai.asr import AsrResult, AsrSegment
from app.ai.documents import Line, attach_references, clean_attendees, format_lines, generate
from app.ai.router import get_ai_task_queue
from app.ai.service import outputs_after_transcription, run_output
from app.ai.usage import Usage
from app.core.config import get_settings
from app.media.pipeline import process_recording
from app.models import AiOutput, AiOutputStatus, Membership, Role, User
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import CleanScanner, Queue, record

pytestmark = pytest.mark.db


CLASS_NOTES: dict[str, Any] = {
    "titulo": "Fotosíntesis",
    "introduccion": "Cómo obtienen energía las plantas.",
    "secciones": [
        {
            "titulo": "Fase luminosa",
            "contenido": "Ocurre en los tilacoides.\nProduce ATP.",
            "segmentos": [0, 42],  # 42 no existe: se descarta
        }
    ],
    "conceptos": [{"termino": "Clorofila", "definicion": "Pigmento verde.", "segmentos": [1]}],
    "ejemplos": [],
    "para_repasar": [{"texto": "¿Dónde ocurre la fase luminosa?", "segmentos": [0]}],
    "deberes": [{"descripcion": "Leer el tema 4", "fecha_limite": "el lunes", "segmentos": [1]}],
}


class FakeLlm:
    name = "fake"
    model = "fake-llm"

    def __init__(self, replies: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.replies = replies
        self.usage = Usage()

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        self.usage.add(1_000, 200)  # tokens simulados por llamada
        if self.replies:
            return self.replies.pop(0)
        if "fragmento de una sesión larga" in system:
            return {"notas": [{"texto": "nota", "segmentos": [0]}], "decisiones": [], "tareas": []}
        if "apuntes de la clase" in system:
            return CLASS_NOTES
        if "acta" in system:
            return {
                "titulo": "Seguimiento",
                "asistentes": ["Laura"],
                "temas": [{"titulo": "Presupuesto", "resumen": "Se pide.", "segmentos": [0]}],
                "decisiones": [],
                "tareas": [
                    {
                        "descripcion": "Preparar el presupuesto",
                        "responsable": "Laura",
                        "fecha_limite": "el viernes",
                        "segmentos": [1, 99],  # 99 no existe: se descarta
                    }
                ],
                "preguntas_abiertas": [],
            }
        return {"resumen": "Reunión breve.", "puntos_clave": [{"texto": "x", "segmentos": [1]}]}


class SpeakerAsr:
    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        return AsrResult(
            language="es",
            segments=[
                AsrSegment(0, 3_000, "Laura, ¿preparas el presupuesto?", 0.9, "A"),
                AsrSegment(3_500, 6_000, "Sí, el viernes.", 0.9, "B"),
            ],
            provider="fake",
            model="fake-diarize",
            processing_ms=10,
        )


class AiQueue:
    def __init__(self) -> None:
        self.generated: list[uuid.UUID] = []

    def generate(self, output_id: uuid.UUID) -> None:
        self.generated.append(output_id)


@pytest.fixture
def queue(app: FastAPI) -> Queue:
    from app.recordings.router import get_task_queue

    q = Queue()
    app.dependency_overrides[get_task_queue] = lambda: q
    return q


@pytest.fixture
def ai_queue(app: FastAPI) -> AiQueue:
    q = AiQueue()
    app.dependency_overrides[get_ai_task_queue] = lambda: q
    return q


@pytest.fixture
def ana(make_user: Callable[..., User], login: Callable[[str], ApiClient]) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


@pytest.fixture
def transcribed(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> dict[str, Any]:
    rec = record(ana, tmp_path)
    process_recording(
        db,
        storage,  # type: ignore[arg-type]
        CleanScanner(),
        SpeakerAsr(),
        redis,
        uuid.UUID(rec["id"]),
    )
    return rec


# --- Documentos (sin BD) -------------------------------------------------------------------


def test_transcript_format_uses_names_and_labels() -> None:
    lines = [Line(0, 5_000, "H1", "Hola."), Line(1, 65_000, "H2", "Buenas."), Line(2, 0, None, "x")]
    assert format_lines(lines, {"H1": "Laura"}).splitlines() == [
        "[0] (00:05) Laura: Hola.",
        "[1] (01:05) Hablante 2: Buenas.",
        "[2] (00:00) x",
    ]


def test_invalid_references_are_dropped_and_timed() -> None:
    content = {"tareas": [{"descripcion": "x", "segmentos": [3, 1, 1, 42, "a"]}]}
    assert attach_references(content, {1: 1_000, 3: 9_000}) == {
        "tareas": [
            {
                "descripcion": "x",
                "segmentos": [{"idx": 1, "start_ms": 1_000}, {"idx": 3, "start_ms": 9_000}],
            }
        ]
    }


def test_long_transcript_uses_map_reduce() -> None:
    lines = [Line(i, i * 1_000, "H1", "palabra " * 200) for i in range(30)]
    notes = {"notas": [{"texto": "n", "segmentos": [0]}], "decisiones": [], "tareas": []}
    final = {"resumen": "r", "puntos_clave": []}
    llm = FakeLlm()
    # Contexto pequeño: obliga a trocear.
    result = generate(llm, "summary", lines, {}, "es", context_tokens=5_000)
    assert result.chunks > 1
    assert len(llm.calls) == result.chunks + 1
    # La última llamada recibe notas con las citas originales, no la transcripción.
    assert llm.calls[-1][1].startswith("[0] Nota:")
    llm2 = FakeLlm([notes] * result.chunks + [final])
    assert generate(llm2, "summary", lines, {}, "es", context_tokens=5_000).content == final


def test_short_transcript_single_call_in_requested_language() -> None:
    llm = FakeLlm()
    generate(llm, "minutes", [Line(0, 0, None, "Hola")], {}, "ca", context_tokens=16_384)
    [(system, user)] = llm.calls
    assert "catalán" in system
    assert user == "[0] (00:00) Hola"


# --- Hablantes -----------------------------------------------------------------------------


def test_speakers_are_normalized_and_can_be_renamed(
    transcribed: dict[str, Any], ana: ApiClient
) -> None:
    t = ana.get(f"/v1/recordings/{transcribed['id']}/transcript").json()
    assert [s["speaker"] for s in t["segments"]] == ["H1", "H2"]
    assert t["speaker_names"] == {}

    res = ana.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/speakers",
        json={"names": {"H1": "  Ana ", "H2": "Laura"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["speaker_names"] == {"H1": "Ana", "H2": "Laura"}

    res = ana.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/speakers", json={"names": {"H1": ""}}
    )
    assert res.json()["speaker_names"] == {"H2": "Laura"}

    res = ana.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/speakers", json={"names": {"H9": "X"}}
    )
    assert res.json()["error"]["code"] == "unknown_speaker"


# --- Generación ----------------------------------------------------------------------------


def test_nothing_is_generated_automatically_by_default(
    transcribed: dict[str, Any], db: Session
) -> None:
    """Resumen y acta son bajo demanda: transcribir no consume el LLM."""
    assert outputs_after_transcription(db, uuid.UUID(transcribed["id"])) == []


def test_summary_and_minutes_after_transcription(
    transcribed: dict[str, Any],
    ana: ApiClient,
    db: Session,
    redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "ai_auto_generate", True)
    ana.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/speakers",
        json={"names": {"H2": "Laura"}},
    )
    ids = outputs_after_transcription(db, uuid.UUID(transcribed["id"]))
    assert len(ids) == 2
    # Solo una vez por versión de transcripción.
    assert outputs_after_transcription(db, uuid.UUID(transcribed["id"])) == []

    llm = FakeLlm()
    for output_id in ids:
        run_output(db, llm, redis, output_id)
    # El modelo recibe los nombres asignados a los hablantes.
    assert "Laura: Sí, el viernes." in llm.calls[0][1]

    outputs = {
        o["kind"]: o for o in ana.get(f"/v1/recordings/{transcribed['id']}/ai-outputs").json()
    }
    assert outputs["summary"]["status"] == "ready"
    assert outputs["summary"]["model"] == "fake-llm"
    task = outputs["minutes"]["content"]["tareas"][0]
    assert task["responsable"] == "Laura"
    assert task["segmentos"] == [{"idx": 1, "start_ms": 3_500}]


def test_regenerate_on_demand(
    transcribed: dict[str, Any], ana: ApiClient, ai_queue: AiQueue, db: Session
) -> None:
    res = ana.post(f"/v1/recordings/{transcribed['id']}/ai-outputs", json={"kind": "summary"})
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "pending"
    assert ai_queue.generated == [uuid.UUID(res.json()["id"])]
    again = ana.post(f"/v1/recordings/{transcribed['id']}/ai-outputs", json={"kind": "summary"})
    assert again.json()["error"]["code"] == "already_generating"
    output = db.get_one(AiOutput, uuid.UUID(res.json()["id"]))
    assert output.status is AiOutputStatus.PENDING


def test_permissions(
    transcribed: dict[str, Any],
    ana: ApiClient,
    ai_queue: AiQueue,
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    make_user("eve@example.com")
    eve = login("eve@example.com")
    assert eve.get(f"/v1/recordings/{transcribed['id']}/ai-outputs").status_code == 404
    res = eve.post(f"/v1/recordings/{transcribed['id']}/ai-outputs", json={"kind": "summary"})
    assert res.status_code == 404
    res = eve.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/speakers", json={"names": {"H1": "x"}}
    )
    assert res.status_code == 404

    viewer = make_user("vic@example.com")
    db.add(
        Membership(
            user_id=viewer.id,
            organization_id=uuid.UUID(transcribed["organization_id"]),
            role=Role.VIEWER,
        )
    )
    db.commit()
    # Espacio personal de Ana: un lector añadido a mano puede leer pero no generar.
    vic = login("vic@example.com")
    assert vic.get(f"/v1/recordings/{transcribed['id']}/ai-outputs").status_code == 200
    res = vic.post(f"/v1/recordings/{transcribed['id']}/ai-outputs", json={"kind": "minutes"})
    assert res.status_code == 403


def test_invented_generic_attendees_are_removed() -> None:
    content = {"asistentes": ["Marta", "Jordi", "Hablante 1", "Hablante 3", "Marta"]}
    assert clean_attendees(content, set())["asistentes"] == ["Marta", "Jordi"]
    assert clean_attendees(content, {"H1", "H2"})["asistentes"] == ["Marta", "Jordi", "Hablante 1"]

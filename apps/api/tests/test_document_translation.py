"""Traducción de resumen, acta y apuntes: textos, endpoints, ciclo de vida y exportación."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.document_translation import (
    mark_document_translation_failed,
    run_document_translation,
    texts,
    with_texts,
)
from app.ai.llm import LlmError
from app.ai.router import get_ai_task_queue
from app.ai.service import create_outputs, latest_transcript, run_output
from app.auth.sessions import now
from app.models import (
    AiOutput,
    AiOutputKind,
    AiOutputTranslation,
    Membership,
    Recording,
    Role,
    UsageRecord,
    User,
)
from app.worker.sweeper import MAX_REQUEUES, sweep
from tests.conftest import ApiClient
from tests.test_ai import FakeLlm, ana, queue, transcribed  # noqa: F401 - fixtures
from tests.test_translation import Translator


def noop(_: object) -> None:
    pass


# --- Textos (sin BD) -----------------------------------------------------------------------

MINUTES: dict[str, Any] = {
    "titulo": "Seguimiento",
    "asistentes": ["Laura", "Pau"],
    "temas": [
        {"titulo": "Presupuesto", "resumen": "Se pide.", "segmentos": [{"idx": 0, "start_ms": 0}]}
    ],
    "decisiones": [],
    "tareas": [
        {
            "descripcion": "Preparar el presupuesto",
            "responsable": "Laura",
            "fecha_limite": None,
            "segmentos": [{"idx": 1, "start_ms": 3_500}],
        }
    ],
    "preguntas_abiertas": [],
}


def test_only_texts_are_translated_and_references_are_kept() -> None:
    found = texts(MINUTES)
    assert [t for _, t in found] == [
        "Seguimiento", "Laura", "Pau", "Presupuesto", "Se pide.",
        "Preparar el presupuesto", "Laura",
    ]  # fmt: skip
    rebuilt = with_texts(MINUTES, {path: f"[T] {t}" for path, t in found})
    assert rebuilt["temas"][0] == {
        "titulo": "[T] Presupuesto",
        "resumen": "[T] Se pide.",
        "segmentos": [{"idx": 0, "start_ms": 0}],
    }
    assert rebuilt["tareas"][0]["fecha_limite"] is None
    assert rebuilt["asistentes"] == ["[T] Laura", "[T] Pau"]
    assert MINUTES["titulo"] == "Seguimiento"  # el original no se toca


# --- Endpoints y ciclo de vida -------------------------------------------------------------


class DocumentQueue:
    def __init__(self) -> None:
        self.generated: list[uuid.UUID] = []
        self.translated: list[uuid.UUID] = []

    def generate(self, output_id: uuid.UUID) -> None:
        self.generated.append(output_id)

    def translate_document(self, translation_id: uuid.UUID) -> None:
        self.translated.append(translation_id)


@pytest.fixture
def documents(app: FastAPI) -> DocumentQueue:
    q = DocumentQueue()
    app.dependency_overrides[get_ai_task_queue] = lambda: q
    return q


@pytest.fixture
def minutes(
    transcribed: dict[str, Any],  # noqa: F811
    db: Session,
    redis: fakeredis.FakeRedis,
) -> AiOutput:
    rec = db.get_one(Recording, uuid.UUID(transcribed["id"]))
    transcript = latest_transcript(db, rec.id)
    assert transcript
    [output] = create_outputs(db, rec, transcript, [AiOutputKind.MINUTES])
    db.commit()
    run_output(db, FakeLlm(), redis, output.id)
    db.refresh(output)
    return output


def ask(c: ApiClient, output: AiOutput, language: str = "en"):  # type: ignore[no-untyped-def]
    return c.post(f"/v1/ai-outputs/{output.id}/translations", json={"language": language})


def listed(c: ApiClient, output: AiOutput) -> list[dict[str, Any]]:
    res = c.get(f"/v1/ai-outputs/{output.id}/translations")
    assert res.status_code == 200, res.text
    return list(res.json())


@pytest.mark.db
def test_translate_a_document(
    minutes: AiOutput,
    ana: ApiClient,  # noqa: F811
    documents: DocumentQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    assert listed(ana, minutes) == []
    res = ask(ana, minutes)
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "pending"
    assert ask(ana, minutes).json()["error"]["code"] == "already_translating"

    llm = Translator()
    run_document_translation(db, llm, redis, documents.translated[0])
    assert "documento" in llm.systems[0]

    [t] = listed(ana, minutes)
    assert (t["language"], t["status"], t["outdated"], t["model"]) == (
        "en", "ready", False, "fake-llm",
    )  # fmt: skip
    content = t["content"]
    assert content["titulo"] == "[T] Seguimiento"
    assert content["tareas"][0]["descripcion"] == "[T] Preparar el presupuesto"
    # Las citas son las del original.
    assert content["tareas"][0]["segmentos"] == [{"idx": 1, "start_ms": 3_500}]
    assert db.scalars(select(UsageRecord).where(UsageRecord.kind == "translation")).one()

    # Editar el documento deja la traducción desactualizada; pedirla otra vez la rehace.
    edited = {**minutes.content, "titulo": "Seguimiento semanal"}
    res = ana.patch(f"/v1/ai-outputs/{minutes.id}", json={"content": edited})
    assert res.status_code == 200, res.text
    assert listed(ana, minutes)[0]["outdated"] is True
    assert ask(ana, minutes).status_code == 202
    db.expire_all()
    run_document_translation(db, Translator(), redis, documents.translated[-1])
    [t] = listed(ana, minutes)
    assert (t["outdated"], t["content"]["titulo"]) == (False, "[T] Seguimiento semanal")


@pytest.mark.db
def test_untranslated_texts_stay_in_the_original_and_nothing_is_a_failure(
    minutes: AiOutput,
    ana: ApiClient,  # noqa: F811
    documents: DocumentQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, minutes)
    [tid] = documents.translated
    # JSONB no conserva el orden de las claves: se busca la posición del título.
    title = next(i for i, (path, _) in enumerate(texts(minutes.content)) if path == ("titulo",))
    run_document_translation(db, Translator(skip={title}), redis, tid)
    assert listed(ana, minutes)[0]["content"]["titulo"] == "Seguimiento"

    ask(ana, minutes)
    db.expire_all()
    with pytest.raises(LlmError) as exc:
        run_document_translation(db, Translator(skip=set(range(20))), redis, tid)
    assert exc.value.transient is False
    mark_document_translation_failed(db, redis, tid, "translation_failed")
    [t] = listed(ana, minutes)
    assert (t["status"], t["error_code"]) == ("failed", "translation_failed")


@pytest.mark.db
def test_rejected_requests_and_permissions(
    minutes: AiOutput,
    ana: ApiClient,  # noqa: F811
    documents: DocumentQueue,
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    assert ask(ana, minutes, "es").json()["error"]["code"] == "same_language"
    assert ask(ana, minutes, "xx").status_code == 422

    make_user("eve@example.com")
    eve = login("eve@example.com")
    assert ask(eve, minutes).status_code == 404
    assert eve.get(f"/v1/ai-outputs/{minutes.id}/translations").status_code == 404

    viewer = make_user("vic@example.com")
    rec = db.get_one(Recording, minutes.recording_id)
    db.add(Membership(user_id=viewer.id, organization_id=rec.organization_id, role=Role.VIEWER))
    db.commit()
    vic = login("vic@example.com")
    assert vic.get(f"/v1/ai-outputs/{minutes.id}/translations").status_code == 200
    assert ask(vic, minutes).status_code == 403
    assert documents.translated == []


@pytest.mark.db
def test_lost_document_translations_are_requeued_and_given_up(
    minutes: AiOutput,
    ana: ApiClient,  # noqa: F811
    documents: DocumentQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, minutes)
    [tid] = documents.translated
    db.execute(update(AiOutputTranslation).values(updated_at=now() - timedelta(minutes=30)))
    db.commit()
    requeued: list[uuid.UUID] = []
    for _ in range(MAX_REQUEUES):
        assert sweep(db, redis, noop, noop, noop, requeued.append)[3] == 1
    assert requeued == [tid] * MAX_REQUEUES
    assert sweep(db, redis, noop, noop, noop, requeued.append)[3] == 0
    [t] = listed(ana, minutes)
    assert (t["status"], t["error_code"]) == ("failed", "translation_stuck")


@pytest.mark.db
def test_export_with_translated_documents(
    minutes: AiOutput,
    ana: ApiClient,  # noqa: F811
    documents: DocumentQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    def export(fmt: str, **params: Any):  # type: ignore[no-untyped-def]
        return ana.get(f"/v1/recordings/{minutes.recording_id}/exports/{fmt}", params=params)

    ask(ana, minutes)
    run_document_translation(db, Translator(), redis, documents.translated[0])

    # Sin la transcripción: basta con que el documento esté traducido.
    md = export("md", language="en", transcript="false")
    assert md.status_code == 200, md.text
    assert "[T] Preparar el presupuesto" in md.text
    assert "Traducción automática (inglés)" in md.text
    assert md.headers["content-disposition"].endswith('-en.md"')
    assert "[T]" not in export("md", transcript="false").text
    # Con la transcripción sin traducir, la traducción falta y se avisa.
    assert export("md", language="en").json()["error"]["code"] == "translation_not_ready"

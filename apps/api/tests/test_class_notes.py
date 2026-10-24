"""Apuntes de clase: generación bajo demanda, edición y exportación."""

import uuid
from typing import Any

import fakeredis
import pytest
from sqlalchemy.orm import Session

from app.ai.documents import PROMPTS, SCHEMAS
from app.ai.service import run_output
from app.models import AiOutputKind
from tests.conftest import ApiClient
from tests.test_ai import (  # noqa: F401 - fixtures
    AiQueue,
    FakeLlm,
    ai_queue,
    ana,
    queue,
    transcribed,
)

pytestmark = pytest.mark.db


@pytest.fixture
def notes(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    ai_queue: AiQueue,  # noqa: F811
    db: Session,
    redis: fakeredis.FakeRedis,
) -> dict[str, Any]:
    res = ana.post(f"/v1/recordings/{transcribed['id']}/ai-outputs", json={"kind": "class_notes"})
    assert res.status_code == 202, res.text
    llm = FakeLlm()
    run_output(db, llm, redis, uuid.UUID(res.json()["id"]))
    # El prompt es el de una clase, no el de una reunión.
    assert "apuntes de la clase" in llm.calls[0][0]
    return transcribed


def test_every_kind_has_prompt_and_schema() -> None:
    for kind in AiOutputKind:
        assert kind.value in PROMPTS
        assert kind.value in SCHEMAS


def test_generate_class_notes(notes: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    outputs = {o["kind"]: o for o in ana.get(f"/v1/recordings/{notes['id']}/ai-outputs").json()}
    # Solo se ha pedido el de apuntes: el resumen y el acta no se generan solos.
    assert list(outputs) == ["class_notes"]
    content = outputs["class_notes"]["content"]
    assert outputs["class_notes"]["status"] == "ready"
    assert content["titulo"] == "Fotosíntesis"
    assert content["secciones"][0]["segmentos"] == [{"idx": 0, "start_ms": 0}]
    assert content["deberes"][0]["segmentos"] == [{"idx": 1, "start_ms": 3_500}]


def test_edit_class_notes(notes: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    [output] = ana.get(f"/v1/recordings/{notes['id']}/ai-outputs").json()
    content = {**output["content"], "titulo": "Fotosíntesis (tema 3)"}
    res = ana.patch(f"/v1/ai-outputs/{output['id']}", json={"content": content})
    assert res.status_code == 200, res.text
    assert res.json()["content"]["titulo"] == "Fotosíntesis (tema 3)"

    # Mismas reglas que el resto de documentos: sin campos desconocidos.
    bad = {**content, "resumen": "no pertenece a los apuntes"}
    res = ana.patch(f"/v1/ai-outputs/{output['id']}", json={"content": bad})
    assert res.status_code == 422


def test_export_class_notes(notes: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    md = ana.get(f"/v1/recordings/{notes['id']}/exports/md").text
    assert "## Apuntes de clase" in md
    assert "### Fase luminosa" in md
    assert "Ocurre en los tilacoides." in md
    assert "Produce ATP. _[00:00]_" in md
    assert "- Clorofila: Pigmento verde. _[00:03]_" in md
    assert "- Leer el tema 4 (Fecha límite: el lunes)" in md

    without = ana.get(
        f"/v1/recordings/{notes['id']}/exports/md", params={"class_notes": "false"}
    ).text
    assert "Apuntes de clase" not in without

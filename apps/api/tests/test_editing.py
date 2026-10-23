"""Edición manual con historial: título, transcripción y documentos de IA."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import Membership, Role, User
from tests.conftest import ApiClient
from tests.test_ai import ana, queue, transcribed  # noqa: F401 - fixtures
from tests.test_exports import documented  # noqa: F401 - fixture

pytestmark = pytest.mark.db


def test_rename_recording(transcribed: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    res = ana.patch(f"/v1/recordings/{transcribed['id']}", json={"title": "  Comité de dirección "})
    assert res.status_code == 200
    assert res.json()["title"] == "Comité de dirección"
    assert ana.patch(f"/v1/recordings/{transcribed['id']}", json={"title": ""}).status_code == 422


def test_edit_segment_text_and_speaker_with_history(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
) -> None:
    base = f"/v1/recordings/{transcribed['id']}/transcript"
    res = ana.patch(f"{base}/segments/1", json={"text": " Sí, el lunes. ", "speaker": "H1"})
    assert res.status_code == 200, res.text
    seg = res.json()["segments"][1]
    assert (seg["text"], seg["speaker"]) == ("Sí, el lunes.", "H1")

    # Sin cambios reales no se añade historial.
    ana.patch(f"{base}/segments/1", json={"text": "Sí, el lunes."})
    history = ana.get(f"{base}/history").json()
    assert {(h["field"], h["old_value"], h["new_value"]) for h in history} == {
        ("text", "Sí, el viernes.", "Sí, el lunes."),
        ("speaker", "H2", "H1"),
    }
    assert history[0]["editor_name"] == "Ana"

    # La búsqueda usa el texto corregido.
    found = ana.get("/v1/recordings", params={"q": "lunes"}).json()
    assert [r["id"] for r in found] == [transcribed["id"]]

    assert ana.patch(f"{base}/segments/1", json={"speaker": "H9"}).status_code == 422
    assert ana.patch(f"{base}/segments/99", json={"text": "x"}).status_code == 404


def test_viewer_cannot_edit(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    viewer = make_user("vic@example.com")
    db.add(
        Membership(
            user_id=viewer.id,
            organization_id=uuid.UUID(transcribed["organization_id"]),
            role=Role.VIEWER,
        )
    )
    db.commit()
    vic = login("vic@example.com")
    base = f"/v1/recordings/{transcribed['id']}"
    assert vic.get(f"{base}/transcript/history").status_code == 200
    assert vic.patch(f"{base}/transcript/segments/0", json={"text": "x"}).status_code == 403
    assert vic.patch(base, json={"title": "x"}).status_code == 403


def _summary(c: ApiClient, rec: dict[str, Any]) -> dict[str, Any]:
    outputs = c.get(f"/v1/recordings/{rec['id']}/ai-outputs").json()
    return next(o for o in outputs if o["kind"] == "summary")


def test_edit_summary_keeps_revisions_and_normalizes_references(
    documented: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
) -> None:
    summary = _summary(ana, documented)
    assert summary["edited_at"] is None
    original = summary["content"]

    edited = {
        "resumen": "Resumen corregido a mano.",
        "puntos_clave": [
            {"texto": "Punto existente", "segmentos": original["puntos_clave"][0]["segmentos"]},
            {"texto": "Punto nuevo", "segmentos": [0, 77]},  # 77 no existe
        ],
    }
    res = ana.patch(f"/v1/ai-outputs/{summary['id']}", json={"content": edited})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["edited_at"] is not None
    assert body["content"]["resumen"] == "Resumen corregido a mano."
    assert body["content"]["puntos_clave"][1]["segmentos"] == [{"idx": 0, "start_ms": 0}]

    revisions = ana.get(f"/v1/ai-outputs/{summary['id']}/revisions").json()
    assert len(revisions) == 1
    assert revisions[0]["content"] == original
    assert revisions[0]["editor_name"] == "Ana"

    # La exportación usa el contenido editado.
    md = ana.get(f"/v1/recordings/{documented['id']}/exports/md").text
    assert "Resumen corregido a mano." in md


def test_invalid_edit_is_rejected(documented: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    summary = _summary(ana, documented)
    bad = {"resumen": "x", "puntos_clave": [], "script": "<b>no</b>"}
    res = ana.patch(f"/v1/ai-outputs/{summary['id']}", json={"content": bad})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"
    assert ana.get(f"/v1/ai-outputs/{summary['id']}/revisions").json() == []


def test_outsider_cannot_edit_or_read_revisions(
    documented: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    summary = _summary(ana, documented)
    make_user("eve@example.com")
    eve = login("eve@example.com")
    content = {"resumen": "x", "puntos_clave": []}
    assert (
        eve.patch(f"/v1/ai-outputs/{summary['id']}", json={"content": content}).status_code == 404
    )
    assert eve.get(f"/v1/ai-outputs/{summary['id']}/revisions").status_code == 404

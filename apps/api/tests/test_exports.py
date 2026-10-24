"""Exportación: Markdown, TXT, SRT, VTT, PDF y DOCX."""

import io
import uuid
from collections.abc import Callable
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.service import create_outputs, latest_transcript, run_output
from app.exports.builders import clock
from app.exports.router import filename
from app.models import AiOutputKind, AuditEvent, Recording, User
from tests.conftest import ApiClient
from tests.test_ai import FakeLlm, ana, queue, transcribed  # noqa: F401 - fixtures

pytestmark = pytest.mark.db


@pytest.fixture
def documented(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    db: Session,
    redis: fakeredis.FakeRedis,
) -> dict[str, Any]:
    """Grabación transcrita, con hablantes nombrados, resumen y acta."""
    ana.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/speakers",
        json={"names": {"H1": "Ana", "H2": "Laura"}},
    )
    rec = db.get_one(Recording, uuid.UUID(transcribed["id"]))
    rec.title = "Reunión de presupuesto: «Q4»"
    db.commit()
    transcript = latest_transcript(db, rec.id)
    assert transcript
    outputs = create_outputs(db, rec, transcript, [AiOutputKind.SUMMARY, AiOutputKind.MINUTES])
    db.commit()
    for output in outputs:
        run_output(db, FakeLlm(), redis, output.id)
    return transcribed


def get(c: ApiClient, rec: dict[str, Any], fmt: str, **params: Any):  # type: ignore[no-untyped-def]
    return c.get(f"/v1/recordings/{rec['id']}/exports/{fmt}", params=params)


def test_clock_formats() -> None:
    assert clock(65_000) == "01:05"
    assert clock(3_725_000) == "1:02:05"
    assert clock(3_725_042, fraction=",") == "01:02:05,042"


def test_filename_is_ascii_slug() -> None:
    assert filename("Reunión de presupuesto: «Q4»", "pdf") == "reunion-de-presupuesto-q4.pdf"
    assert filename("¿¿??", "md") == "grabacion.md"


def test_markdown_has_all_parts(documented: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    res = get(ana, documented, "md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert 'filename="reunion-de-presupuesto-q4.md"' in res.headers["content-disposition"]
    text = res.text
    assert text.startswith("# Reunión de presupuesto: «Q4»")
    for heading in ("## Resumen", "### Puntos clave", "## Acta de la reunión", "## Transcripción"):
        assert heading in text
    assert (
        "- Preparar el presupuesto (Responsable: Laura · Fecha límite: el viernes) _[00:03]_"
        in text
    )
    assert "**[00:00]** Ana: Laura, ¿preparas el presupuesto?" in text
    assert "**[00:03]** Laura: Sí, el viernes." in text


def test_parts_can_be_excluded(documented: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    text = get(ana, documented, "md", transcript=False, summary=False).text
    assert "## Acta de la reunión" in text
    assert "## Resumen" not in text
    assert "## Transcripción" not in text


def test_subtitles(documented: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    srt = get(ana, documented, "srt").text
    assert srt.splitlines()[:3] == [
        "1",
        "00:00:00,000 --> 00:00:03,000",
        "Ana: Laura, ¿preparas el presupuesto?",
    ]
    vtt = get(ana, documented, "vtt").text
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\n")
    txt = get(ana, documented, "txt").text
    assert "[00:03] Laura: Sí, el viernes." in txt


def test_pdf_and_docx(documented: dict[str, Any], ana: ApiClient) -> None:  # noqa: F811
    pdf = get(ana, documented, "pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    from docx import Document

    docx = get(ana, documented, "docx")
    assert docx.content.startswith(b"PK")
    paragraphs = [p.text for p in Document(io.BytesIO(docx.content)).paragraphs]
    assert "Acta de la reunión" in paragraphs
    assert any("Laura: Sí, el viernes." in p for p in paragraphs)


def test_export_is_audited(
    documented: dict[str, Any],
    ana: ApiClient,  # noqa: F811
    db: Session,
) -> None:
    get(ana, documented, "pdf", transcript=False)
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.exported")).one()
    assert event.event_metadata == {
        "format": "pdf",
        "transcript": False,
        "summary": True,
        "minutes": True,
        "class_notes": False,
    }


def test_nothing_to_export_and_isolation(
    documented: dict[str, Any],
    ana: ApiClient,  # noqa: F811
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    org_id = ana.get("/v1/organizations").json()[0]["id"]
    empty = ana.post(
        "/v1/recordings", json={"organization_id": org_id, "title": "Vacía", "consent": True}
    ).json()
    assert get(ana, empty, "md").json()["error"]["code"] == "nothing_to_export"
    assert get(ana, documented, "exe").status_code == 422

    make_user("eve@example.com")
    assert get(login("eve@example.com"), documented, "pdf").status_code == 404

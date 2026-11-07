"""Unificar hablantes que el diarizador separó (un audio largo se diariza por trozos)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from sqlalchemy.orm import Session

from app.ai.asr import AsrResult, AsrSegment
from app.ai.llm import LlmError
from app.ai.speaker_merge import has_candidates, mergeable, spans, suggest
from app.ai.usage import Usage
from app.media.pipeline import process_recording
from app.models import User
from app.recordings import router as recordings_router
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import CleanScanner, Queue, queue, record  # noqa: F401 - fixtures


@dataclass
class Said:
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str


# Dos "trozos": en el primero hablan H1 y H2; en el segundo, H3 y H4.
SAID = [
    Said(0, 1_000, "H1", "Hola, soy Laura y llevo el presupuesto."),
    Said(1_000, 2_000, "H2", "Gracias, Laura."),
    Said(2_000, 3_000, "H1", "El presupuesto sube un 5 %."),
    Said(6_000, 7_000, "H3", "Sigo con el presupuesto: el 5 % se justifica así."),
    Said(7_000, 8_000, "H4", "¿Y los plazos?"),
    Said(8_000, 9_000, "H3", "Los plazos los cierro yo."),
]


class FakeLlm:
    name = "fake"
    model = "fake-llm"

    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[str] = []
        self.usage = Usage()

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(user)
        self.usage.add(100, 20)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_only_labels_that_never_overlap_can_be_merged() -> None:
    found = spans(SAID)
    assert mergeable(["H1", "H3"], found)
    assert not mergeable(["H1", "H2"], found)  # hablan a la vez en el mismo trozo
    assert not mergeable(["H1", "H9"], found)
    assert not mergeable(["H1"], found)


def test_a_single_piece_has_nothing_to_merge() -> None:
    llm = FakeLlm({"grupos": [{"etiquetas": ["H1", "H2"], "nombre": None, "motivo": "x"}]})
    assert not has_candidates(spans(SAID[:3]))
    assert suggest(llm, SAID[:3], {}, "es") == []
    assert llm.calls == []  # sin candidatos no se paga una llamada


def test_suggestions_outside_the_rules_are_dropped() -> None:
    llm = FakeLlm(
        {
            "grupos": [
                {"etiquetas": ["H3", "H1"], "nombre": "Laura", "motivo": "Lleva el presupuesto."},
                {"etiquetas": ["H1", "H4"], "nombre": None, "motivo": "H1 ya está en otro grupo."},
                {"etiquetas": ["H2", "H9"], "nombre": None, "motivo": "No existe H9."},
                {"etiquetas": ["H2"], "nombre": None, "motivo": "Uno solo."},
            ]
        }
    )
    [only] = suggest(llm, SAID, {}, "es")
    assert (only.labels, only.name) == (["H1", "H3"], "Laura")
    # El modelo recibe tramos y frases de cada etiqueta, no la transcripción entera.
    assert "H3: habla de 00:06 a 00:09, 2 intervenciones" in llm.calls[0]


class SpeakerAsr:
    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        return AsrResult(
            language="es",
            segments=[
                AsrSegment(s.start_ms, s.end_ms, s.text, speaker=f"x{s.speaker}") for s in SAID
            ],
            provider="fake",
            model="fake-1",
            processing_ms=1,
        )


@pytest.fixture
def ana(make_user: Callable[..., User], login: Callable[[str], ApiClient]) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


@pytest.fixture
def rec_id(
    ana: ApiClient,
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> str:
    rec = record(ana, tmp_path)
    process_recording(db, storage, CleanScanner(), SpeakerAsr(), redis, uuid.UUID(rec["id"]))  # type: ignore[arg-type]
    return str(rec["id"])


@pytest.mark.db
def test_merge_reassigns_turns_keeps_history_and_name(ana: ApiClient, rec_id: str) -> None:
    ana.patch(f"/v1/recordings/{rec_id}/transcript/speakers", json={"names": {"H3": "Laura"}})
    res = ana.post(
        f"/v1/recordings/{rec_id}/transcript/speakers/merge", json={"labels": ["H3", "H1"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Queda la etiqueta que aparece antes, con el nombre que tenía la otra.
    assert [s["speaker"] for s in body["segments"]] == ["H1", "H2", "H1", "H1", "H4", "H1"]
    assert body["speaker_names"] == {"H1": "Laura"}

    history = ana.get(f"/v1/recordings/{rec_id}/transcript/history").json()
    assert sorted((h["old_value"], h["new_value"]) for h in history) == [("H3", "H1")] * 2


@pytest.mark.db
def test_merge_rejects_unknown_labels(ana: ApiClient, rec_id: str) -> None:
    res = ana.post(
        f"/v1/recordings/{rec_id}/transcript/speakers/merge", json={"labels": ["H1", "H9"]}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "unknown_speaker"


@pytest.mark.db
def test_suggestions_endpoint_proposes_without_changing_anything(
    ana: ApiClient, rec_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLlm(
        {"grupos": [{"etiquetas": ["H1", "H3"], "nombre": "Laura", "motivo": "Presupuesto."}]}
    )
    monkeypatch.setattr(recordings_router, "get_llm_provider", lambda: llm)
    res = ana.post(f"/v1/recordings/{rec_id}/transcript/speaker-suggestions")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "suggestions": [{"labels": ["H1", "H3"], "name": "Laura", "reason": "Presupuesto."}]
    }
    speakers = {
        s["speaker"] for s in ana.get(f"/v1/recordings/{rec_id}/transcript").json()["segments"]
    }
    assert speakers == {"H1", "H2", "H3", "H4"}


@pytest.mark.db
def test_suggestions_report_a_failing_model(
    ana: ApiClient, rec_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLlm(LlmError("caído", transient=True))
    monkeypatch.setattr(recordings_router, "get_llm_provider", lambda: llm)
    res = ana.post(f"/v1/recordings/{rec_id}/transcript/speaker-suggestions")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "suggestions_failed"

"""Traducción de transcripciones: modelo, ciclo de vida, endpoints y exportación."""

import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.documents import Line
from app.ai.llm import LlmError
from app.ai.service import latest_transcript
from app.ai.translation import CHUNK_LINES, mark_translation_failed, run_translation, translate
from app.ai.translation_router import get_translation_queue
from app.ai.usage import Usage
from app.auth.sessions import now
from app.models import (
    Membership,
    Role,
    TranscriptTranslation,
    UsageRecord,
    User,
)
from app.worker.sweeper import MAX_REQUEUES, sweep
from tests.conftest import ApiClient
from tests.test_ai import ana, queue, transcribed  # noqa: F401 - fixtures


class Translator:
    """Traduce anteponiendo "[T] ". `skip`: frases que no devuelve nunca."""

    name = "fake"
    model = "fake-llm"

    def __init__(self, skip: set[int] | None = None, fail_at: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.systems: list[str] = []
        self.skip = skip or set()
        self.fail_at = fail_at
        self.usage = Usage()

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompt = json.loads(user)
        self.calls.append(prompt)
        self.systems.append(system)
        if self.fail_at == len(self.calls):
            raise LlmError("sin conexión", transient=True)
        self.usage.add(100, 50)
        return {
            "frases": [
                {"n": f["n"], "texto": f"[T] {f['texto']}"}
                for f in prompt["frases"]
                if f["n"] not in self.skip
            ]
            # Lo que no se ha pedido se descarta.
            + [{"n": 9_999, "texto": "inventada"}, {"n": "x", "texto": "y"}]
        }


def lines(count: int) -> list[Line]:
    return [Line(i, i * 1_000, "H1", f"frase {i}") for i in range(count)]


def run(llm: Any, all_lines: list[Line], todo: list[Line] | None = None) -> dict[int, str]:
    out: dict[int, str] = {}
    for done in translate(
        llm, all_lines, todo or all_lines, {"H1": "Laura"}, "es", "en", context_tokens=100_000
    ):
        out |= done
    return out


# --- Modelo (sin BD) -----------------------------------------------------------------------


def test_every_line_is_translated_in_chunks_with_context() -> None:
    llm = Translator()
    result = run(llm, lines(CHUNK_LINES + 5))

    assert result == {i: f"[T] frase {i}" for i in range(CHUNK_LINES + 5)}
    assert [len(c["frases"]) for c in llm.calls] == [CHUNK_LINES, 5]
    assert llm.calls[0]["contexto"] == []
    assert [c["texto"] for c in llm.calls[1]["contexto"]] == ["frase 37", "frase 38", "frase 39"]
    assert llm.calls[1]["frases"][0] == {"n": 40, "habla": "Laura", "texto": "frase 40"}
    assert "inglés" in llm.systems[0] and "español" in llm.systems[0]


def test_small_context_makes_smaller_chunks() -> None:
    llm = Translator()
    long = [Line(i, 0, None, "palabra " * 100) for i in range(6)]
    list(translate(llm, long, long, {}, "es", "en", context_tokens=2_500))
    assert len(llm.calls) == 6


def test_missing_lines_are_asked_again_and_then_left_untranslated() -> None:
    llm = Translator(skip={1})
    assert run(llm, lines(3)) == {0: "[T] frase 0", 2: "[T] frase 2"}
    assert [[f["n"] for f in c["frases"]] for c in llm.calls] == [[0, 1, 2], [1], [1]]


def test_multiline_text_survives_the_prompt() -> None:
    llm = Translator()
    assert run(llm, [Line(0, 0, None, 'una\n"dos"')]) == {0: '[T] una\n"dos"'}


# --- Endpoints y ciclo de vida -------------------------------------------------------------


class TranslationQueue:
    def __init__(self) -> None:
        self.requested: list[uuid.UUID] = []

    def translate(self, translation_id: uuid.UUID) -> None:
        self.requested.append(translation_id)


@pytest.fixture
def translations(app: FastAPI) -> TranslationQueue:
    q = TranslationQueue()
    app.dependency_overrides[get_translation_queue] = lambda: q
    return q


def ask(c: ApiClient, rec: dict[str, Any], language: str = "en"):  # type: ignore[no-untyped-def]
    return c.post(f"/v1/recordings/{rec['id']}/translations", json={"language": language})


def detail(c: ApiClient, rec: dict[str, Any], language: str = "en") -> dict[str, Any]:
    res = c.get(f"/v1/recordings/{rec['id']}/translations/{language}")
    assert res.status_code == 200, res.text
    return dict(res.json())


@pytest.mark.db
def test_translate_on_demand(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    url = f"/v1/recordings/{transcribed['id']}/translations"
    assert ana.get(url).json() == []
    assert ana.get(f"{url}/en").json()["error"]["code"] == "translation_not_found"

    res = ask(ana, transcribed)
    assert res.status_code == 202, res.text
    assert (res.json()["status"], res.json()["translated"], res.json()["total"]) == (
        "pending",
        0,
        2,
    )
    assert translations.requested == [uuid.UUID(res.json()["id"])]
    assert ask(ana, transcribed).json()["error"]["code"] == "already_translating"

    run_translation(db, Translator(), redis, translations.requested[0])

    [listed] = ana.get(url).json()
    assert (listed["language"], listed["status"], listed["model"]) == ("en", "ready", "fake-llm")
    assert (listed["translated"], listed["total"]) == (2, 2)
    assert detail(ana, transcribed)["segments"] == [
        {"idx": 0, "text": "[T] Laura, ¿preparas el presupuesto?", "outdated": False},
        {"idx": 1, "text": "[T] Sí, el viernes.", "outdated": False},
    ]
    usage = db.scalars(select(UsageRecord).where(UsageRecord.kind == "translation")).one()
    assert (usage.input_tokens, usage.output_tokens) == (100, 50)

    # La transcripción original no cambia.
    t = ana.get(f"/v1/recordings/{transcribed['id']}/transcript").json()
    assert t["segments"][1]["text"] == "Sí, el viernes."


@pytest.mark.db
def test_only_corrected_lines_are_translated_again(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, transcribed)
    run_translation(db, Translator(), redis, translations.requested[0])
    ana.patch(
        f"/v1/recordings/{transcribed['id']}/transcript/segments/1", json={"text": "Sí, el jueves."}
    )
    before = detail(ana, transcribed)
    assert (before["translated"], before["segments"][1]["outdated"]) == (1, True)

    assert ask(ana, transcribed).status_code == 202
    llm = Translator()
    db.expire_all()
    run_translation(db, llm, redis, translations.requested[0])
    assert [[f["n"] for f in c["frases"]] for c in llm.calls] == [[1]]
    after = detail(ana, transcribed)
    assert (after["translated"], after["segments"][1]["text"]) == (2, "[T] Sí, el jueves.")

    # Al día: pedirla otra vez la traduce entera.
    ask(ana, transcribed)
    llm = Translator()
    db.expire_all()
    run_translation(db, llm, redis, translations.requested[0])
    assert len(llm.calls[0]["frases"]) == 2


@pytest.mark.db
def test_finished_chunks_survive_a_failure(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.ai.translation.CHUNK_LINES", 1)
    ask(ana, transcribed)
    [tid] = translations.requested
    with pytest.raises(LlmError):
        run_translation(db, Translator(fail_at=2), redis, tid)
    assert ana.get(f"/v1/recordings/{transcribed['id']}/translations").json()[0]["translated"] == 1
    assert db.scalars(select(UsageRecord).where(UsageRecord.kind == "translation")).one()

    llm = Translator()
    run_translation(db, llm, redis, tid)  # el reintento sigue donde se quedó
    assert [[f["n"] for f in c["frases"]] for c in llm.calls] == [[1]]
    assert detail(ana, transcribed)["status"] == "ready"


@pytest.mark.db
def test_nothing_translated_is_a_failure(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, transcribed)
    [tid] = translations.requested
    with pytest.raises(LlmError) as exc:
        run_translation(db, Translator(skip={0, 1}), redis, tid)
    assert exc.value.transient is False
    mark_translation_failed(db, redis, tid, "translation_failed")
    failed = ana.get(f"/v1/recordings/{transcribed['id']}/translations").json()[0]
    assert (failed["status"], failed["error_code"]) == ("failed", "translation_failed")
    # Se puede volver a pedir.
    assert ask(ana, transcribed).json()["status"] == "pending"


@pytest.mark.db
def test_recording_deleted_while_translating(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, transcribed)

    class Deleting(Translator):
        def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            assert ana.delete(f"/v1/recordings/{transcribed['id']}").status_code == 204
            return super().complete_json(system, user, schema)

    run_translation(db, Deleting(), redis, translations.requested[0])
    assert db.scalars(select(TranscriptTranslation)).all() == []


@pytest.mark.db
def test_rejected_requests_and_permissions(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    assert ask(ana, transcribed, "es").json()["error"]["code"] == "same_language"
    assert ask(ana, transcribed, "xx").status_code == 422

    make_user("eve@example.com")
    eve = login("eve@example.com")
    assert ask(eve, transcribed).status_code == 404
    assert eve.get(f"/v1/recordings/{transcribed['id']}/translations").status_code == 404
    assert eve.get(f"/v1/recordings/{transcribed['id']}/translations/en").status_code == 404

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
    assert vic.get(f"/v1/recordings/{transcribed['id']}/translations").status_code == 200
    assert ask(vic, transcribed).status_code == 403
    assert translations.requested == []


@pytest.mark.db
def test_a_new_transcript_version_starts_without_translations(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, transcribed)
    run_translation(db, Translator(), redis, translations.requested[0])
    old = latest_transcript(db, uuid.UUID(transcribed["id"]))
    assert old is not None
    db.add(
        type(old)(
            recording_id=old.recording_id,
            version=old.version + 1,
            language="es",
            provider="fake",
            model="fake",
            content="",
            processing_ms=0,
        )
    )
    db.commit()
    assert ana.get(f"/v1/recordings/{transcribed['id']}/translations").json() == []


# --- Exportación ---------------------------------------------------------------------------


@pytest.mark.db
def test_export_translated_transcript(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    def export(fmt: str, **params: Any):  # type: ignore[no-untyped-def]
        return ana.get(f"/v1/recordings/{transcribed['id']}/exports/{fmt}", params=params)

    assert export("srt", language="en").json()["error"]["code"] == "translation_not_ready"
    ask(ana, transcribed)
    assert export("srt", language="en").status_code == 409  # aún pendiente
    # La segunda frase no se traduce: se exporta en el idioma original.
    run_translation(db, Translator(skip={1}), redis, translations.requested[0])

    srt = export("srt", language="en")
    assert srt.headers["content-disposition"].endswith('-en.srt"')
    assert "[T] Laura, ¿preparas el presupuesto?" in srt.text
    assert "Hablante 2: Sí, el viernes." in srt.text
    for fmt in ("txt", "md"):
        assert "Traducción automática (inglés)" in export(fmt, language="en").text
    for fmt in ("pdf", "docx"):
        assert export(fmt, language="en").status_code == 200

    original = export("md")
    assert "[T]" not in original.text and "Traducción automática" not in original.text
    # Pedir el idioma original equivale a no pedir traducción.
    assert "[T]" not in export("md", language="es").text


@pytest.mark.db
def test_lost_translations_are_requeued_and_finally_given_up(
    transcribed: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    translations: TranslationQueue,
    db: Session,
    redis: fakeredis.FakeRedis,
) -> None:
    ask(ana, transcribed)
    [tid] = translations.requested
    db.execute(
        update(TranscriptTranslation).values(updated_at=now() - timedelta(minutes=30))
    )  # fmt: skip
    db.commit()

    requeued: list[uuid.UUID] = []
    for _ in range(MAX_REQUEUES):
        assert sweep(db, redis, lambda _: None, lambda _: None, requeued.append)[2] == 1
    assert requeued == [tid] * MAX_REQUEUES
    assert sweep(db, redis, lambda _: None, lambda _: None, requeued.append)[2] == 0
    failed = ana.get(f"/v1/recordings/{transcribed['id']}/translations").json()[0]
    assert (failed["status"], failed["error_code"]) == ("failed", "translation_stuck")

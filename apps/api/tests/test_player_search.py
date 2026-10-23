"""Reproducción del audio (Range) y búsqueda en transcripciones."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.asr import AsrResult, AsrSegment
from app.media.pipeline import process_recording
from app.models import AuditEvent, User
from app.recordings.audio import parse_range
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import CleanScanner, Queue, record

pytestmark = pytest.mark.db


class TextAsr:
    def __init__(self, *texts: str, speaker: str | None = None) -> None:
        self.texts = texts
        self.speaker = speaker

    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        return AsrResult(
            language="es",
            segments=[
                AsrSegment(i * 3_000, i * 3_000 + 2_500, t, 0.9, self.speaker)
                for i, t in enumerate(self.texts)
            ],
            provider="fake",
            model="fake",
            processing_ms=1,
        )


@pytest.fixture
def queue(app: Any) -> Queue:
    from app.recordings.router import get_task_queue

    q = Queue()
    app.dependency_overrides[get_task_queue] = lambda: q
    return q


@pytest.fixture
def ana(make_user: Callable[..., User], login: Callable[[str], ApiClient]) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


def transcribed(
    c: ApiClient,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
    asr: TextAsr,
    title: str = "Reunión",
) -> dict[str, Any]:
    rec = record(c, tmp_path)
    if title != "Reunión":
        from app.models import Recording

        db.get_one(Recording, uuid.UUID(rec["id"])).title = title
        db.commit()
    process_recording(db, storage, CleanScanner(), asr, redis, uuid.UUID(rec["id"]))  # type: ignore[arg-type]
    return rec


# --- Range -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("bytes=0-99", (0, 99)),
        ("bytes=100-", (100, 999)),
        ("bytes=-10", (990, 999)),
        ("bytes=900-5000", (900, 999)),
    ],
)
def test_parse_range(header: str | None, expected: tuple[int, int] | None) -> None:
    assert parse_range(header, 1_000) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=5-2", "items=0-1", "bytes=-"])
def test_parse_range_rejects_invalid(header: str) -> None:
    with pytest.raises(ValueError):
        parse_range(header, 1_000)


def test_audio_streaming_with_ranges(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    assert ana.get(f"/v1/recordings/{rec['id']}/audio").json()["error"]["code"] == "audio_not_ready"
    process_recording(db, storage, CleanScanner(), TextAsr("Hola"), redis, uuid.UUID(rec["id"]))  # type: ignore[arg-type]
    key = next(k for k in storage.objects if k.endswith("/audio/source.m4a"))
    audio = storage.get(key)

    full = ana.get(f"/v1/recordings/{rec['id']}/audio")
    assert full.status_code == 200
    assert full.content == audio
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "audio/mp4"

    part = ana.get(f"/v1/recordings/{rec['id']}/audio", headers={"Range": "bytes=10-109"})
    assert part.status_code == 206
    assert part.content == audio[10:110]
    assert part.headers["content-range"] == f"bytes 10-109/{len(audio)}"

    bad = ana.get(f"/v1/recordings/{rec['id']}/audio", headers={"Range": f"bytes={len(audio)}-"})
    assert bad.status_code == 416

    # El reproductor pide muchos trozos: un único evento de auditoría.
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "recording.audio_accessed")
    ).all()
    assert len(events) == 1


def test_outsider_cannot_stream_audio(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    tmp_path: Path,
) -> None:
    rec = transcribed(ana, db, storage, redis, tmp_path, TextAsr("Hola"))
    make_user("eve@example.com")
    assert login("eve@example.com").get(f"/v1/recordings/{rec['id']}/audio").status_code == 404


# --- Búsqueda --------------------------------------------------------------------------------


def test_search_by_transcript_title_speaker_and_date(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    tmp_path: Path,
) -> None:
    budget_asr = TextAsr(
        "Buenos días.", "Hay que revisar el presupuesto.", "El presupuesto sube.", speaker="A"
    )
    budget = transcribed(ana, db, storage, redis, tmp_path, budget_asr)
    launch_asr = TextAsr("Lanzamos el 15 de noviembre.")
    launch = transcribed(ana, db, storage, redis, tmp_path, launch_asr, title="Plan de lanzamiento")
    ana.patch(f"/v1/recordings/{budget['id']}/transcript/speakers", json={"names": {"H1": "Marta"}})

    found = ana.get("/v1/recordings", params={"q": "presupuesto"}).json()
    assert [r["id"] for r in found] == [budget["id"]]
    # Mismo hablante: las tres frases forman un único turno (idx 0).
    [match] = found[0]["matches"]
    assert (match["idx"], match["start_ms"]) == (0, 0)
    assert match["highlighted"].count("⟦presupuesto⟧") == 2

    by_title = ana.get("/v1/recordings", params={"q": "lanzamiento"}).json()
    assert [r["id"] for r in by_title] == [launch["id"]]

    by_speaker = ana.get("/v1/recordings", params={"speaker": "mar"}).json()
    assert [r["id"] for r in by_speaker] == [budget["id"]]

    assert ana.get("/v1/recordings", params={"q": "inexistente"}).json() == []
    assert ana.get("/v1/recordings", params={"date_from": "2099-01-01T00:00:00Z"}).json() == []
    assert len(ana.get("/v1/recordings", params={"date_to": "2099-01-01T00:00:00Z"}).json()) == 2

    # Sin acceso a la organización, la búsqueda no revela nada.
    make_user("eve@example.com")
    assert login("eve@example.com").get("/v1/recordings", params={"q": "presupuesto"}).json() == []


def test_search_special_characters_are_literal(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    transcribed(ana, db, storage, redis, tmp_path, TextAsr("Hola"), title="100% listo")
    transcribed(ana, db, storage, redis, tmp_path, TextAsr("Hola"), title="Otra")
    assert len(ana.get("/v1/recordings", params={"q": "100%"}).json()) == 1
    assert ana.get("/v1/recordings", params={"q": "%"}).status_code == 200


def test_search_mixes_recordings_with_different_transcript_versions(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    """Regresión: la "última versión" se calculaba sobre todas las grabaciones a la vez, y
    solo aparecían las que tenían la versión más alta del sistema."""
    single = transcribed(ana, db, storage, redis, tmp_path, TextAsr("Revisamos el presupuesto."))
    twice = transcribed(ana, db, storage, redis, tmp_path, TextAsr("Sin coincidencias."))
    ana.post(f"/v1/recordings/{twice['id']}/reprocess", json={})
    process_recording(
        db, storage, CleanScanner(), TextAsr("Ahora sí, el presupuesto."), redis,  # type: ignore[arg-type]
        uuid.UUID(twice["id"]),
    )  # fmt: skip
    found = {r["id"] for r in ana.get("/v1/recordings", params={"q": "presupuesto"}).json()}
    assert found == {single["id"], twice["id"]}
    # Solo cuenta la última versión: la v1 de "twice" no mencionaba el presupuesto.
    v1_only = ana.get("/v1/recordings", params={"q": "coincidencias"}).json()
    assert v1_only == []

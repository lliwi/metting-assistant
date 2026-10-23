"""Pipeline de procesamiento: unir la captura (o preparar la importación) y transcribir."""

import hashlib
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.asr import AsrError, AsrResult, AsrSegment
from app.media.antivirus import ScanResult
from app.media.ffmpeg import probe
from app.media.pipeline import process_recording
from app.models import (
    AudioSegment,
    AuditEvent,
    Membership,
    Recording,
    RecordingStatus,
    Role,
    User,
)
from app.recordings.router import get_task_queue
from tests.conftest import ApiClient, MemoryStorage

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg no instalado"),
]


class Queue:
    def __init__(self) -> None:
        self.processed: list[uuid.UUID] = []

    def process(self, recording_id: uuid.UUID) -> None:
        self.processed.append(recording_id)


class FakeAsr:
    def __init__(self, error: AsrError | None = None) -> None:
        self.calls: list[tuple[float, str | None]] = []
        self.error = error

    def transcribe(self, path: Path, language: str | None) -> AsrResult:
        self.calls.append((probe(path).duration_ms / 1000, language))
        if self.error:
            raise self.error
        return AsrResult(
            language=language or "es",
            segments=[
                AsrSegment(0, 2_500, "Buenos días a todos.", 0.91),
                AsrSegment(2_500, 6_000, "Laura prepara el presupuesto.", 0.87),
            ],
            provider="fake",
            model="fake-1",
            processing_ms=1234,
        )


class CleanScanner:
    def scan(self, path: Path) -> ScanResult:
        return ScanResult(clean=True)


@pytest.fixture
def queue(app: FastAPI) -> Queue:
    q = Queue()
    app.dependency_overrides[get_task_queue] = lambda: q
    return q


@pytest.fixture
def ana(make_user: Callable[..., User], login: Callable[[str], ApiClient]) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


def webm_segment(tmp_path: Path, seq: int, seconds: int = 3) -> bytes:
    path = tmp_path / f"seg{seq}.webm"
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={300 + seq * 100}:duration={seconds}",
            "-ac",
            "2",
            "-c:a",
            "libopus",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def record(c: ApiClient, tmp_path: Path, org_id: str | None = None) -> dict[str, Any]:
    """Graba 3 segmentos en la pista 0 y 1 en la pista 1, y cierra la captura."""
    org_id = org_id or c.get("/v1/organizations").json()[0]["id"]
    rec = c.post(
        "/v1/recordings", json={"organization_id": org_id, "title": "R", "consent": True}
    ).json()
    c.post(f"/v1/recordings/{rec['id']}/tracks")
    for track, seq in [(0, 0), (0, 1), (0, 2), (1, 0)]:
        data = webm_segment(tmp_path, track * 10 + seq)
        res = c.post(
            f"/v1/recordings/{rec['id']}/segments",
            data={
                "segment_id": str(uuid.uuid4()),
                "track": str(track),
                "seq": str(seq),
                "duration_ms": "3000",
                "sha256": hashlib.sha256(data).hexdigest(),
            },
            files={"file": ("s.webm", data, "audio/webm")},
        )
        assert res.status_code == 201, res.text
    res = c.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 3, "1": 1}})
    assert res.status_code == 200, res.text
    return dict(rec)


def run(
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    rec_id: str,
    asr: FakeAsr,
) -> Recording:
    process_recording(db, storage, CleanScanner(), asr, redis, uuid.UUID(rec_id))  # type: ignore[arg-type]
    db.expire_all()
    return db.get_one(Recording, uuid.UUID(rec_id))


def test_browser_capture_is_joined_and_transcribed(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    assert queue.processed == [uuid.UUID(rec["id"])]

    asr = FakeAsr()
    result = run(db, storage, redis, rec["id"], asr)

    # Un único audio con las dos pistas seguidas (4 segmentos de 3 s).
    [(seconds, language)] = asr.calls
    assert 11.5 <= seconds <= 12.5
    assert language == "es"
    assert result.status is RecordingStatus.TRANSCRIBED
    assert result.audio_object_key and result.audio_object_key.endswith("/audio/source.m4a")
    assert 11_500 <= result.duration_ms <= 12_500
    # Los segmentos originales se borran una vez unido el audio.
    assert list(storage.objects) == [result.audio_object_key]
    assert db.scalars(select(AudioSegment)).all() == []

    transcript = ana.get(f"/v1/recordings/{rec['id']}/transcript").json()
    assert transcript["version"] == 1
    assert (transcript["provider"], transcript["model"]) == ("fake", "fake-1")
    assert [(s["start_ms"], s["text"]) for s in transcript["segments"]] == [
        (0, "Buenos días a todos."),
        (2_500, "Laura prepara el presupuesto."),
    ]
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.transcribed")).one()
    assert event.event_metadata["processing_ms"] == 1234

    # Idempotente: ya transcrita, no se vuelve a procesar.
    run(db, storage, redis, rec["id"], asr)
    assert len(asr.calls) == 1


def test_import_is_prepared_then_transcribed(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    data = webm_segment(tmp_path, 0, seconds=5)
    org_id = ana.get("/v1/organizations").json()[0]["id"]
    rec = ana.post(
        "/v1/recordings",
        json={"organization_id": org_id, "title": "I", "consent": True, "source": "import"},
    ).json()
    ana.post(
        f"/v1/recordings/{rec['id']}/segments",
        data={
            "segment_id": str(uuid.uuid4()),
            "track": "0",
            "seq": "0",
            "duration_ms": "0",
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        files={"file": ("a.webm", data, "audio/webm")},
    )
    ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 1}})

    asr = FakeAsr()
    result = run(db, storage, redis, rec["id"], asr)
    assert result.status is RecordingStatus.TRANSCRIBED
    assert 4.5 <= asr.calls[0][0] <= 5.5


def test_permanent_asr_error_propagates_without_transcript(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    with pytest.raises(AsrError):
        run(db, storage, redis, rec["id"], FakeAsr(AsrError("modelo roto", transient=False)))
    db.rollback()
    assert ana.get(f"/v1/recordings/{rec['id']}/transcript").status_code == 404


def test_reprocess_creates_new_version(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    run(db, storage, redis, rec["id"], FakeAsr())

    res = ana.post(f"/v1/recordings/{rec['id']}/reprocess", json={"language": "ca"})
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "pending"
    assert queue.processed[-1] == uuid.UUID(rec["id"])
    # Mientras está pendiente no se puede volver a pedir.
    again = ana.post(f"/v1/recordings/{rec['id']}/reprocess", json={})
    assert again.json()["error"]["code"] == "not_reprocessable"

    asr = FakeAsr()
    run(db, storage, redis, rec["id"], asr)
    assert asr.calls[0][1] == "ca"
    assert ana.get(f"/v1/recordings/{rec['id']}/transcript").json()["version"] == 2
    v1 = ana.get(f"/v1/recordings/{rec['id']}/transcript", params={"version": 1}).json()
    assert v1["language"] == "es"


@pytest.mark.parametrize(("role", "allowed"), [(Role.MEMBER, False), (Role.ADMIN, True)])
def test_reprocess_permissions(
    role: Role,
    allowed: bool,
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    tmp_path: Path,
) -> None:
    org_id = ana.post("/v1/organizations", json={"name": "Equipo"}).json()["id"]
    rec = record(ana, tmp_path, org_id)
    run(db, storage, redis, rec["id"], FakeAsr())
    bob = make_user("bob@example.com")
    db.add(Membership(user_id=bob.id, organization_id=uuid.UUID(org_id), role=role))
    db.commit()
    res = login("bob@example.com").post(f"/v1/recordings/{rec['id']}/reprocess", json={})
    assert (res.status_code == 202) is allowed


def test_outsider_cannot_read_transcript(
    ana: ApiClient,
    queue: Queue,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    run(db, storage, redis, rec["id"], FakeAsr())
    make_user("eve@example.com")
    eve = login("eve@example.com")
    assert eve.get(f"/v1/recordings/{rec['id']}/transcript").status_code == 404
    assert eve.post(f"/v1/recordings/{rec['id']}/reprocess", json={}).status_code == 404

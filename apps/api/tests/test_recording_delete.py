"""Eliminar una grabación: borrado en cascada, purga del audio y permisos."""

import uuid
from collections.abc import Callable
from pathlib import Path

import fakeredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Membership,
    Recording,
    RecordingStatus,
    Role,
    Transcript,
    TranscriptEdit,
    UsageRecord,
    User,
)
from app.worker.tasks import purge_storage_prefix
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import FakeAsr, Queue, ana, queue, record, run  # noqa: F401 - fixtures

pytestmark = pytest.mark.db


def count(db: Session, model: type, **where: object) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return int(db.scalar(query) or 0)


def test_owner_deletes_recording_and_its_data(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    run(db, storage, redis, rec["id"], FakeAsr())
    res = ana.patch(
        f"/v1/recordings/{rec['id']}/transcript/segments/0", json={"text": "Buenos días."}
    )
    assert res.status_code == 200, res.text
    rec_id = uuid.UUID(rec["id"])
    usage = db.scalars(select(UsageRecord).where(UsageRecord.recording_id == rec_id)).one()
    assert count(db, TranscriptEdit) == 1

    res = ana.delete(f"/v1/recordings/{rec['id']}")
    assert res.status_code == 204, res.text

    db.expire_all()
    assert db.get(Recording, rec_id) is None
    assert count(db, Transcript, recording_id=rec_id) == 0
    assert count(db, TranscriptEdit) == 0
    # El consumo de IA se conserva para la facturación, sin la referencia a la grabación.
    db.refresh(usage)
    assert usage.recording_id is None
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.deleted")).one()
    assert event.resource_id == rec["id"]
    assert event.event_metadata["title"] == "R"
    assert queue.purged == [f"orgs/{rec['organization_id']}/recordings/{rec['id']}/"]
    assert ana.get(f"/v1/recordings/{rec['id']}").status_code == 404
    assert ana.delete(f"/v1/recordings/{rec['id']}").status_code == 404


def test_cannot_delete_while_processing(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    db.get_one(Recording, uuid.UUID(rec["id"])).status = RecordingStatus.PROCESSING
    db.commit()
    res = ana.delete(f"/v1/recordings/{rec['id']}")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "recording_busy"
    assert queue.purged == []


@pytest.mark.parametrize(
    ("role", "status"),
    [(Role.VIEWER, 403), (Role.MEMBER, 403), (Role.ADMIN, 204)],
)
def test_who_can_delete_someone_elses_recording(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    tmp_path: Path,
    role: Role,
    status: int,
) -> None:
    team = ana.post("/v1/organizations", json={"name": "Equipo"}).json()
    rec = record(ana, tmp_path, org_id=team["id"])
    user = make_user("max@example.com")
    db.add(Membership(user_id=user.id, organization_id=uuid.UUID(team["id"]), role=role))
    db.commit()
    assert login("max@example.com").delete(f"/v1/recordings/{rec['id']}").status_code == status

    make_user("eve@example.com")
    assert login("eve@example.com").delete(f"/v1/recordings/{rec['id']}").status_code == 404


def test_purge_task_removes_only_that_recording(
    storage: MemoryStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.worker.tasks.get_storage", lambda: storage)
    org, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    storage.put(f"orgs/{org}/recordings/{a}/audio/source.m4a", b"x", "audio/mp4")
    storage.put(f"orgs/{org}/recordings/{a}/tracks/0/00000-x.webm", b"x", "audio/webm")
    storage.put(f"orgs/{org}/recordings/{b}/audio/source.m4a", b"y", "audio/mp4")
    assert purge_storage_prefix(f"orgs/{org}/recordings/{a}/") == 2
    assert list(storage.objects) == [f"orgs/{org}/recordings/{b}/audio/source.m4a"]
    for bad in (
        f"orgs/{org}/recordings/",
        f"orgs/{org}/recordings/{b}",
        f"orgs/{org}/recordings/{str(b)[:8]}/",
        f"orgs/{org}/exports/{b}/",
        f"orgs/{org}/recordings/{b}/audio/",
    ):
        with pytest.raises(ValueError):
            purge_storage_prefix(bad)
    assert len(storage.objects) == 1

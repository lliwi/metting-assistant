"""Mover una grabación a otra organización: audio, documentos, coste y permisos."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Membership, Recording, RecordingStatus, Role, UsageRecord, User
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import FakeAsr, Queue, ana, queue, record, run  # noqa: F401 - fixtures

pytestmark = pytest.mark.db


def org(c: ApiClient, name: str) -> str:
    res = c.post("/v1/organizations", json={"name": name})
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def move(c: ApiClient, rec_id: str, org_id: str) -> Any:
    return c.post(f"/v1/recordings/{rec_id}/move", json={"organization_id": org_id})


def test_move_takes_audio_documents_and_cost(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    source, target = org(ana, "Origen"), org(ana, "Destino")
    rec = record(ana, tmp_path, org_id=source)
    run(db, storage, redis, rec["id"], FakeAsr())
    rec_id = uuid.UUID(rec["id"])
    old_key = db.get_one(Recording, rec_id).audio_object_key
    assert old_key and old_key.startswith(f"orgs/{source}/recordings/{rec_id}/")

    res = move(ana, rec["id"], target)
    assert res.status_code == 200, res.text
    assert res.json()["organization_id"] == target

    db.expire_all()
    moved = db.get_one(Recording, rec_id)
    new_key = f"orgs/{target}/recordings/{rec_id}/" + old_key.split(f"/{rec_id}/", 1)[1]
    assert moved.organization_id == uuid.UUID(target)
    assert moved.audio_object_key == new_key
    assert storage.objects[new_key] == storage.objects[old_key]
    # El audio antiguo se purga después en el worker.
    assert queue.purged == [f"orgs/{source}/recordings/{rec_id}/"]
    # El coste pasa a la organización de destino.
    usage = db.scalars(select(UsageRecord).where(UsageRecord.recording_id == rec_id)).all()
    assert usage and all(u.organization_id == uuid.UUID(target) for u in usage)
    month = moved.created_at.strftime("%Y-%m")
    cost = ana.get(f"/v1/organizations/{target}/usage", params={"month": month}).json()
    assert cost["items"]
    assert not ana.get(f"/v1/organizations/{source}/usage", params={"month": month}).json()["items"]
    # Transcripción y audio siguen accesibles desde la grabación.
    assert ana.get(f"/v1/recordings/{rec['id']}/transcript").status_code == 200
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.moved")).one()
    assert event.organization_id == uuid.UUID(target)
    assert event.event_metadata["from"] == source


def test_cannot_move_while_capturing_or_to_same_org(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    tmp_path: Path,
) -> None:
    source, target = org(ana, "Origen"), org(ana, "Destino")
    rec = record(ana, tmp_path, org_id=source)  # cerrada, pendiente de transcribir
    res = move(ana, rec["id"], target)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "recording_busy"

    db.get_one(Recording, uuid.UUID(rec["id"])).status = RecordingStatus.TRANSCRIBED
    db.commit()
    assert move(ana, rec["id"], source).json()["error"]["code"] == "same_organization"
    assert queue.purged == []


@pytest.mark.parametrize(
    ("source_role", "target_role", "status"),
    [
        (Role.MEMBER, Role.MEMBER, 403),  # grabación ajena sin ser administrador
        (Role.ADMIN, Role.VIEWER, 403),  # sin permiso para grabar en el destino
        (Role.ADMIN, None, 404),  # no es miembro del destino
        (Role.ADMIN, Role.MEMBER, 200),
    ],
)
def test_permissions(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    tmp_path: Path,
    source_role: Role,
    target_role: Role | None,
    status: int,
) -> None:
    source, target = org(ana, "Origen"), org(ana, "Destino")
    rec = record(ana, tmp_path, org_id=source)
    db.get_one(Recording, uuid.UUID(rec["id"])).status = RecordingStatus.TRANSCRIBED
    max_ = make_user("max@example.com")
    db.add(Membership(user_id=max_.id, organization_id=uuid.UUID(source), role=source_role))
    if target_role:
        db.add(Membership(user_id=max_.id, organization_id=uuid.UUID(target), role=target_role))
    db.commit()
    assert move(login("max@example.com"), rec["id"], target).status_code == status

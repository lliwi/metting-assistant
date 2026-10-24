"""Retención: borrado automático de las grabaciones fuera del plazo de su organización."""

import uuid
from datetime import timedelta
from pathlib import Path

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import now
from app.models import AuditEvent, Organization, Recording, RecordingStatus, UsageRecord
from app.worker.retention import apply_retention
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import FakeAsr, Queue, ana, queue, record, run  # noqa: F401 - fixtures

pytestmark = pytest.mark.db


def aged(db: Session, rec: dict[str, object], days: int, **changes: object) -> uuid.UUID:
    row = db.get_one(Recording, uuid.UUID(str(rec["id"])))
    row.created_at = now() - timedelta(days=days)
    for name, value in changes.items():
        setattr(row, name, value)
    db.commit()
    return row.id


def test_deletes_only_expired_recordings(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    team = ana.post("/v1/organizations", json={"name": "Equipo"}).json()
    res = ana.patch(f"/v1/organizations/{team['id']}", json={"retention_days": 30})
    assert res.status_code == 200, res.text

    expired_rec = record(ana, tmp_path, org_id=team["id"])
    run(db, storage, redis, expired_rec["id"], FakeAsr())
    expired = aged(db, expired_rec, 31)
    recent = aged(db, record(ana, tmp_path, org_id=team["id"]), 29)
    busy = aged(db, record(ana, tmp_path, org_id=team["id"]), 90, status=RecordingStatus.PROCESSING)
    # El espacio personal no tiene retención: nada caduca.
    personal = aged(db, record(ana, tmp_path), 3650)

    purged: list[str] = []
    assert apply_retention(db, purged.append) == 1

    db.expire_all()
    assert db.get(Recording, expired) is None
    assert all(db.get(Recording, r) for r in (recent, busy, personal))
    assert purged == [f"orgs/{team['id']}/recordings/{expired}/"]
    usage = db.scalars(select(UsageRecord)).all()
    assert usage and all(u.recording_id is None for u in usage)
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.deleted")).one()
    assert event.actor_id is None
    assert event.resource_id == str(expired)
    assert event.event_metadata["reason"] == "retention"

    # Sin nada caducado, no borra ni purga.
    assert apply_retention(db, purged.append) == 0
    assert len(purged) == 1


def test_zero_disables_retention(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    tmp_path: Path,
) -> None:
    team = ana.post("/v1/organizations", json={"name": "Equipo"}).json()
    ana.patch(f"/v1/organizations/{team['id']}", json={"retention_days": 30})
    ana.patch(f"/v1/organizations/{team['id']}", json={"retention_days": 0})
    assert db.get_one(Organization, uuid.UUID(team["id"])).retention_days is None
    rec = aged(db, record(ana, tmp_path, org_id=team["id"]), 400)

    assert apply_retention(db, lambda _: None) == 0
    assert db.get(Recording, rec) is not None

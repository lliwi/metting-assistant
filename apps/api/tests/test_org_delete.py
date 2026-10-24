"""Eliminar una organización: borrado en cascada, purga del almacenamiento y permisos."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Membership, Organization, Recording, Role, User
from app.orgs.router import get_org_tasks
from app.worker.tasks import purge_storage_prefix
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import Queue, ana, queue, record  # noqa: F401 - fixtures

pytestmark = pytest.mark.db


class FakeOrgTasks:
    def __init__(self) -> None:
        self.purged: list[str] = []

    def purge_storage(self, prefix: str) -> None:
        self.purged.append(prefix)


@pytest.fixture
def org_tasks(app: FastAPI) -> FakeOrgTasks:
    tasks = FakeOrgTasks()
    app.dependency_overrides[get_org_tasks] = lambda: tasks
    return tasks


@pytest.fixture
def team(ana: ApiClient) -> dict[str, Any]:  # noqa: F811
    res = ana.post("/v1/organizations", json={"name": "Equipo"})
    assert res.status_code == 201, res.text
    return dict(res.json())


def delete(c: ApiClient, org_id: str, name: str) -> Any:
    return c.request("DELETE", f"/v1/organizations/{org_id}", json={"confirm_name": name})


def test_owner_deletes_organization_and_its_data(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    team: dict[str, Any],
    org_tasks: FakeOrgTasks,
    db: Session,
    storage: MemoryStorage,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path, org_id=team["id"])
    prefix = f"orgs/{team['id']}/"
    assert any(k.startswith(prefix) for k in storage.objects)

    res = delete(ana, team["id"], " Equipo ")
    assert res.status_code == 204, res.text

    org_id = uuid.UUID(team["id"])
    assert db.get(Organization, org_id) is None
    assert db.get(Recording, uuid.UUID(rec["id"])) is None
    count = select(func.count()).select_from(Membership).where(Membership.organization_id == org_id)
    assert db.scalar(count) == 0
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "org.delete")).one()
    assert event.event_metadata == {"name": "Equipo", "recordings": 1}
    assert org_tasks.purged == [prefix]
    assert ana.get(f"/v1/organizations/{team['id']}").status_code == 404


def test_purge_task_removes_only_that_prefix(
    storage: MemoryStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.worker.tasks.get_storage", lambda: storage)
    a, b = uuid.uuid4(), uuid.uuid4()
    storage.put(f"orgs/{a}/recordings/1/audio/source.m4a", b"x", "audio/mp4")
    storage.put(f"orgs/{b}/recordings/2/audio/source.m4a", b"y", "audio/mp4")
    assert purge_storage_prefix(f"orgs/{a}/") == 1
    assert list(storage.objects) == [f"orgs/{b}/recordings/2/audio/source.m4a"]
    for bad in ("orgs/", f"orgs/{b}", f"orgs/{b}/recordings/", "orgs/x/", f"tmp/{b}/"):
        with pytest.raises(ValueError):
            purge_storage_prefix(bad)
    assert len(storage.objects) == 1


def test_confirmation_and_personal_space(
    ana: ApiClient,  # noqa: F811
    team: dict[str, Any],
    org_tasks: FakeOrgTasks,
) -> None:
    res = delete(ana, team["id"], "Otro nombre")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "confirmation_mismatch"

    personal = next(m for m in ana.get("/v1/auth/me").json()["memberships"] if m["is_personal"])
    res = delete(ana, personal["organization_id"], "x")
    assert res.json()["error"]["code"] == "personal_organization"
    assert org_tasks.purged == []


@pytest.mark.parametrize(("role", "status"), [(Role.ADMIN, 403), (Role.MEMBER, 403)])
def test_only_owners_can_delete(
    team: dict[str, Any],
    org_tasks: FakeOrgTasks,
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
    role: Role,
    status: int,
) -> None:
    user = make_user("max@example.com")
    db.add(Membership(user_id=user.id, organization_id=uuid.UUID(team["id"]), role=role))
    db.commit()
    assert delete(login("max@example.com"), team["id"], "Equipo").status_code == status
    make_user("eve@example.com")
    assert delete(login("eve@example.com"), team["id"], "Equipo").status_code == 404
    assert org_tasks.purged == []

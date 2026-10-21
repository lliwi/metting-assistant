"""Aislamiento entre organizaciones y matriz de permisos.

Criterio de la Fase 1: un usuario de la organización A recibe 404 en cualquier recurso de B.
"""

import itertools
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from app.models import Membership, Role, User
from app.orgs.permissions import ROLE_PERMISSIONS, Action
from tests.conftest import ApiClient, MemoryOutbox

pytestmark = pytest.mark.db

Login = Callable[[str], ApiClient]
MakeUser = Callable[..., User]


@pytest.fixture
def team(make_user: MakeUser, login: Login) -> tuple[ApiClient, str]:
    """Ana es propietaria de la organización "Equipo"."""
    make_user("ana@example.com")
    ana = login("ana@example.com")
    org = ana.post("/v1/organizations", json={"name": "Equipo"}).json()
    return ana, org["id"]


def add_member(db: Session, org_id: str, user: User, role: Role) -> None:
    db.add(Membership(user_id=user.id, organization_id=uuid.UUID(org_id), role=role))
    db.commit()


def test_outsider_gets_404_on_every_org_endpoint(
    team: tuple[ApiClient, str], make_user: MakeUser, login: Login
) -> None:
    ana, org_id = team
    ana_id = ana.get("/v1/auth/me").json()["id"]
    make_user("eve@example.com")
    eve = login("eve@example.com")

    requests = [
        ("GET", f"/v1/organizations/{org_id}", None),
        ("PATCH", f"/v1/organizations/{org_id}", {"name": "Hackeada"}),
        ("GET", f"/v1/organizations/{org_id}/members", None),
        ("PATCH", f"/v1/organizations/{org_id}/members/{ana_id}", {"role": "viewer"}),
        ("DELETE", f"/v1/organizations/{org_id}/members/{ana_id}", None),
        ("GET", f"/v1/organizations/{org_id}/invitations", None),
        ("POST", f"/v1/organizations/{org_id}/invitations", {"email": "eve@example.com"}),
        ("GET", f"/v1/organizations/{org_id}/audit-events", None),
    ]
    for method, url, body in requests:
        res = eve.request(method, url, json=body)
        assert res.status_code == 404, (method, url, res.text)
        assert res.json()["error"]["code"] == "not_found"

    # La organización no aparece en su listado y nada ha cambiado.
    assert org_id not in {o["id"] for o in eve.get("/v1/organizations").json()}
    assert ana.get(f"/v1/organizations/{org_id}").json()["name"] == "Equipo"


def test_nonexistent_org_and_foreign_org_are_indistinguishable(
    team: tuple[ApiClient, str], make_user: MakeUser, login: Login
) -> None:
    _, org_id = team
    make_user("eve@example.com")
    eve = login("eve@example.com")
    foreign = eve.get(f"/v1/organizations/{org_id}")
    missing = eve.get(f"/v1/organizations/{uuid.uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


def test_unauthenticated_gets_401(client: ApiClient) -> None:
    assert client.get("/v1/organizations").status_code == 401


@pytest.mark.parametrize("role", [Role.VIEWER, Role.MEMBER])
def test_non_admin_members_cannot_manage(
    role: Role, team: tuple[ApiClient, str], db: Session, make_user: MakeUser, login: Login
) -> None:
    _, org_id = team
    add_member(db, org_id, make_user("bob@example.com"), role)
    bob = login("bob@example.com")

    assert bob.get(f"/v1/organizations/{org_id}").json()["role"] == role.value
    assert bob.get(f"/v1/organizations/{org_id}/members").status_code == 200
    res = bob.patch(f"/v1/organizations/{org_id}", json={"name": "X"})
    assert res.status_code == 403
    res = bob.post(f"/v1/organizations/{org_id}/invitations", json={"email": "x@example.com"})
    assert res.status_code == 403
    assert bob.get(f"/v1/organizations/{org_id}/audit-events").status_code == 403


def test_admin_cannot_touch_owners(
    team: tuple[ApiClient, str], db: Session, make_user: MakeUser, login: Login
) -> None:
    ana, org_id = team
    ana_id = ana.get("/v1/auth/me").json()["id"]
    bob = make_user("bob@example.com")
    add_member(db, org_id, bob, Role.ADMIN)
    carol = make_user("carol@example.com")
    add_member(db, org_id, carol, Role.MEMBER)
    bob_client = login("bob@example.com")

    members = f"/v1/organizations/{org_id}/members"
    assert bob_client.patch(f"{members}/{carol.id}", json={"role": "viewer"}).status_code == 200
    assert bob_client.patch(f"{members}/{carol.id}", json={"role": "owner"}).status_code == 403
    assert bob_client.patch(f"{members}/{ana_id}", json={"role": "member"}).status_code == 403
    assert bob_client.delete(f"{members}/{ana_id}").status_code == 403
    res = bob_client.post(
        f"/v1/organizations/{org_id}/invitations", json={"email": "x@example.com", "role": "owner"}
    )
    assert res.status_code == 403


def test_last_owner_cannot_leave_or_be_demoted(team: tuple[ApiClient, str]) -> None:
    ana, org_id = team
    ana_id = ana.get("/v1/auth/me").json()["id"]
    res = ana.patch(f"/v1/organizations/{org_id}/members/{ana_id}", json={"role": "admin"})
    assert res.json()["error"]["code"] == "last_owner"
    res = ana.delete(f"/v1/organizations/{org_id}/members/{ana_id}")
    assert res.json()["error"]["code"] == "last_owner"


def test_member_can_leave(
    team: tuple[ApiClient, str], db: Session, make_user: MakeUser, login: Login
) -> None:
    _, org_id = team
    bob = make_user("bob@example.com")
    add_member(db, org_id, bob, Role.VIEWER)
    bob_client = login("bob@example.com")
    assert bob_client.delete(f"/v1/organizations/{org_id}/members/{bob.id}").status_code == 204
    assert bob_client.get(f"/v1/organizations/{org_id}").status_code == 404


def test_personal_org_does_not_accept_invitations(make_user: MakeUser, login: Login) -> None:
    make_user("ana@example.com")
    ana = login("ana@example.com")
    personal = ana.get("/v1/organizations").json()[0]
    assert personal["is_personal"] is True
    res = ana.post(
        f"/v1/organizations/{personal['id']}/invitations", json={"email": "bob@example.com"}
    )
    assert res.json()["error"]["code"] == "personal_organization"


def test_invitation_flow(
    team: tuple[ApiClient, str], outbox: MemoryOutbox, make_user: MakeUser, login: Login
) -> None:
    ana, org_id = team
    res = ana.post(
        f"/v1/organizations/{org_id}/invitations",
        json={"email": "Bob@Example.com", "role": "admin"},
    )
    assert res.status_code == 201
    token = outbox.token_for("bob@example.com")

    make_user("eve@example.com")
    eve = login("eve@example.com")
    res = eve.post("/v1/invitations/accept", json={"token": token})
    assert res.json()["error"]["code"] == "invitation_email_mismatch"

    make_user("bob@example.com")
    bob = login("bob@example.com")
    res = bob.post("/v1/invitations/accept", json={"token": token})
    assert res.status_code == 200
    assert res.json()["role"] == "admin"
    assert bob.post("/v1/invitations/accept", json={"token": token}).status_code == 400

    actions = [e["action"] for e in ana.get(f"/v1/organizations/{org_id}/audit-events").json()]
    assert {"org.create", "org.invitation_created", "org.invitation_accepted"} <= set(actions)


def test_invitation_requires_verified_email(
    team: tuple[ApiClient, str],
    db: Session,
    outbox: MemoryOutbox,
    make_user: MakeUser,
    login: Login,
) -> None:
    ana, org_id = team
    ana.post(f"/v1/organizations/{org_id}/invitations", json={"email": "bob@example.com"})
    token = outbox.token_for("bob@example.com")
    bob = make_user("bob@example.com")
    bob_client = login("bob@example.com")
    bob.email_verified_at = None
    db.commit()
    res = bob_client.post("/v1/invitations/accept", json={"token": token})
    assert res.status_code == 403


def test_revoked_invitation_cannot_be_accepted(
    team: tuple[ApiClient, str], outbox: MemoryOutbox, make_user: MakeUser, login: Login
) -> None:
    ana, org_id = team
    inv = ana.post(
        f"/v1/organizations/{org_id}/invitations", json={"email": "bob@example.com"}
    ).json()
    token = outbox.token_for("bob@example.com")
    assert ana.delete(f"/v1/organizations/{org_id}/invitations/{inv['id']}").status_code == 204
    make_user("bob@example.com")
    res = login("bob@example.com").post("/v1/invitations/accept", json={"token": token})
    assert res.status_code == 400


def test_permission_matrix_is_monotonic() -> None:
    """Cada rol tiene, al menos, los permisos del rol inferior."""
    order = [Role.VIEWER, Role.MEMBER, Role.ADMIN, Role.OWNER]
    for lower, higher in itertools.pairwise(order):
        assert ROLE_PERMISSIONS[lower] <= ROLE_PERMISSIONS[higher]
    assert Action.ORG_DELETE in ROLE_PERMISSIONS[Role.OWNER]
    assert Action.ORG_DELETE not in ROLE_PERMISSIONS[Role.ADMIN]

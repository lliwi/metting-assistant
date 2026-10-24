"""Gestión de cuentas por línea de comandos (`python -m app.auth.cli`)."""

import io
from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.cli import CliError, create_user, run, set_status
from app.models import Membership, Organization, Role, User, UserStatus
from tests.conftest import PASSWORD, ApiClient

pytestmark = pytest.mark.db


def login(client: ApiClient, email: str, password: str = PASSWORD) -> int:
    res = client.post("/v1/auth/login", json={"email": email, "password": password})
    return res.status_code


def test_created_account_can_log_in(client: ApiClient, db: Session) -> None:
    user = create_user(db, email="Ana@Example.com", name=" Ana ", password=PASSWORD, locale="ca")
    assert (user.email, user.name, user.locale) == ("ana@example.com", "Ana", "ca")
    assert user.status is UserStatus.ACTIVE
    assert user.email_verified_at is not None

    assert login(client, "ana@example.com") == 200
    me = client.get("/v1/auth/me").json()
    assert [m["is_personal"] for m in me["memberships"]] == [True]


def test_create_validates_input(db: Session, make_user: Callable[..., User]) -> None:
    make_user("ana@example.com")
    with pytest.raises(CliError, match="Ya existe"):
        create_user(db, email="ANA@example.com", name="Ana", password=PASSWORD)
    with pytest.raises(CliError, match="entre 12"):
        create_user(db, email="bob@example.com", name="Bob", password="corta")


def test_org_is_created_or_reused(db: Session) -> None:
    ana = create_user(db, email="ana@example.com", name="Ana", password=PASSWORD, org="Equipo")
    bob = create_user(
        db, email="bob@example.com", name="Bob", password=PASSWORD, org="equipo", role=Role.VIEWER
    )
    org = db.scalars(select(Organization).where(Organization.is_personal.is_(False))).one()
    roles = {
        m.user_id: m.role
        for m in db.scalars(select(Membership).where(Membership.organization_id == org.id))
    }
    # Quien crea la organización es su propietario; el resto, con el rol pedido.
    assert roles == {ana.id: Role.OWNER, bob.id: Role.VIEWER}


def test_set_password_from_stdin_closes_sessions(
    client: ApiClient,
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_user("ana@example.com")
    assert login(client, "ana@example.com") == 200

    new_password = "una frase nueva bastante larga"
    monkeypatch.setattr("sys.stdin", io.StringIO(new_password + "\n"))
    assert run(["set-password", "ana@example.com", "--password-stdin"], db=db) == 0

    assert client.get("/v1/auth/me").status_code == 401  # sesión anterior cerrada
    assert login(client, "ana@example.com") == 401
    assert login(client, "ana@example.com", new_password) == 200


def test_generated_password_is_shown_once(
    client: ApiClient, db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["create", "eva@example.com", "--name", "Eva", "--generate-password"], db=db) == 0
    password = capsys.readouterr().out.split("Contraseña: ")[1].strip()
    assert len(password) >= 12
    assert login(client, "eva@example.com", password) == 200


def test_disable_and_enable(client: ApiClient, db: Session, make_user: Callable[..., User]) -> None:
    make_user("ana@example.com")
    assert run(["disable", "ana@example.com"], db=db) == 0
    assert login(client, "ana@example.com") != 200

    # enable también activa cuentas que quedaron pendientes de verificar.
    pending = make_user("bob@example.com", verified=False)
    assert pending.status is UserStatus.PENDING
    set_status(db, "bob@example.com", UserStatus.ACTIVE)
    assert run(["enable", "ana@example.com"], db=db) == 0
    assert login(client, "ana@example.com") == 200
    assert login(client, "bob@example.com") == 200


def test_errors_exit_with_code_1(db: Session, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["disable", "nadie@example.com"], db=db) == 1
    assert "No existe" in capsys.readouterr().err


def test_list(db: Session, capsys: pytest.CaptureFixture[str]) -> None:
    create_user(db, email="ana@example.com", name="Ana", password=PASSWORD, org="Equipo")
    assert run(["list"], db=db) == 0
    out = capsys.readouterr().out
    assert "ana@example.com" in out
    assert "Equipo (owner)" in out

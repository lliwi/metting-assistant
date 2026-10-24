"""Gestión de cuentas desde la línea de comandos (no hay registro público).

    docker compose exec api python -m app.auth.cli create ana@example.com --name "Ana"
    docker compose exec api python -m app.auth.cli create bob@example.com --name "Bob" \\
        --org "Equipo" --role member
    docker compose exec api python -m app.auth.cli list
    docker compose exec api python -m app.auth.cli set-password ana@example.com
    docker compose exec api python -m app.auth.cli disable ana@example.com

Sin --password-stdin, la contraseña se pide por teclado (dos veces) o se genera con
--generate-password y se muestra una sola vez.
"""

import argparse
import getpass
import secrets
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth.schemas import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from app.auth.service import find_user_by_email, normalize_email
from app.auth.sessions import now, revoke_all_sessions
from app.core.security import hash_password
from app.db import get_sessionmaker
from app.models import Membership, Organization, Role, User, UserStatus
from app.orgs.service import create_organization, create_personal_organization


class CliError(Exception):
    """Error de uso: se muestra el mensaje y se sale con código 1."""


def check_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise CliError(
            f"La contraseña debe tener entre {PASSWORD_MIN_LENGTH} y "
            f"{PASSWORD_MAX_LENGTH} caracteres"
        )
    return password


def generate_password() -> str:
    # 18 bytes → 24 caracteres URL-safe: ~144 bits de entropía.
    return secrets.token_urlsafe(18)


def _user(db: Session, email: str) -> User:
    user = find_user_by_email(db, email)
    if user is None:
        raise CliError(f"No existe ninguna cuenta con el email {email}")
    return user


def find_organization(db: Session, ref: str) -> Organization | None:
    """Por id o por nombre exacto (sin distinguir mayúsculas) entre las no personales."""
    try:
        return db.get(Organization, uuid.UUID(ref))
    except ValueError:
        pass
    matches = db.scalars(
        select(Organization).where(
            func.lower(Organization.name) == ref.strip().lower(),
            Organization.is_personal.is_(False),
        )
    ).all()
    if len(matches) > 1:
        raise CliError(f"Hay varias organizaciones llamadas «{ref}»: indica su id")
    return matches[0] if matches else None


def create_user(
    db: Session,
    *,
    email: str,
    name: str,
    password: str,
    locale: str = "es",
    org: str | None = None,
    role: Role = Role.MEMBER,
) -> User:
    """Cuenta activa y con el email verificado, con su organización personal. Con `org`, se
    añade a esa organización (se crea si no existe, con el usuario como propietario)."""
    if find_user_by_email(db, email) is not None:
        raise CliError(f"Ya existe una cuenta con el email {email}")
    name = name.strip()
    if not name:
        raise CliError("El nombre no puede estar vacío")
    user = User(
        email=normalize_email(email),
        name=name,
        locale=locale,
        status=UserStatus.ACTIVE,
        email_verified_at=now(),
        password_hash=hash_password(check_password(password)),
    )
    db.add(user)
    db.flush()
    create_personal_organization(db, user)
    if org:
        add_to_organization(db, user, org, role)
    record_audit_event(
        db,
        action="auth.user_created",
        actor_id=None,
        resource_type="user",
        resource_id=user.id,
        metadata={"method": "cli"},
    )
    db.commit()
    return user


def add_to_organization(db: Session, user: User, org_ref: str, role: Role) -> Organization:
    org = find_organization(db, org_ref)
    if org is None:
        org = create_organization(db, user, org_ref.strip())
        if role is not Role.OWNER:
            # Una organización nueva necesita un propietario: es quien la crea.
            print(f"La organización «{org.name}» no existía: creada con {user.email} como owner")
        return org
    membership = db.get(Membership, {"user_id": user.id, "organization_id": org.id})
    if membership is None:
        db.add(Membership(user_id=user.id, organization_id=org.id, role=role))
    else:
        membership.role = role
    db.flush()
    return org


def set_password(db: Session, email: str, password: str) -> User:
    """Fija la contraseña y cierra todas las sesiones abiertas de la cuenta."""
    user = _user(db, email)
    user.password_hash = hash_password(check_password(password))
    revoke_all_sessions(db, user.id)
    record_audit_event(
        db,
        action="auth.password_set",
        resource_type="user",
        resource_id=user.id,
        metadata={"method": "cli"},
    )
    db.commit()
    return user


def set_status(db: Session, email: str, status: UserStatus) -> User:
    user = _user(db, email)
    user.status = status
    if status is UserStatus.ACTIVE:
        # También activa cuentas que quedaron pendientes de verificar el email.
        user.email_verified_at = user.email_verified_at or now()
    if status is UserStatus.DISABLED:
        revoke_all_sessions(db, user.id)
    record_audit_event(
        db,
        action=f"auth.user_{status.value}",
        resource_type="user",
        resource_id=user.id,
        metadata={"method": "cli"},
    )
    db.commit()
    return user


def list_users(db: Session) -> list[tuple[User, list[tuple[str, Role]]]]:
    users = db.scalars(select(User).order_by(User.created_at)).all()
    rows = []
    for user in users:
        orgs = db.execute(
            select(Organization.name, Membership.role)
            .join(Membership, Membership.organization_id == Organization.id)
            .where(Membership.user_id == user.id, Organization.is_personal.is_(False))
            .order_by(Organization.name)
        ).all()
        rows.append((user, [(name, role) for name, role in orgs]))
    return rows


# --- Línea de comandos ---------------------------------------------------------------------


def _read_password(args: argparse.Namespace) -> tuple[str, bool]:
    """(contraseña, generada)."""
    if args.generate_password:
        return generate_password(), True
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n"), False
    if not sys.stdin.isatty():
        raise CliError("Sin terminal: usa --password-stdin o --generate-password")
    first = getpass.getpass("Contraseña: ")
    if first != getpass.getpass("Repite la contraseña: "):
        raise CliError("Las contraseñas no coinciden")
    return first, False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.auth.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def password_options(p: argparse.ArgumentParser) -> None:
        group = p.add_mutually_exclusive_group()
        group.add_argument(
            "--password-stdin", action="store_true", help="lee la contraseña de la entrada estándar"
        )
        group.add_argument(
            "--generate-password",
            action="store_true",
            help="genera una contraseña aleatoria y la muestra",
        )

    create = commands.add_parser("create", help="crea una cuenta activa")
    create.add_argument("email")
    create.add_argument("--name", required=True)
    create.add_argument("--locale", choices=["es", "ca"], default="es")
    create.add_argument("--org", help="organización (id o nombre); se crea si no existe")
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.MEMBER.value)
    password_options(create)

    add = commands.add_parser("add-to-org", help="añade una cuenta a una organización")
    add.add_argument("email")
    add.add_argument("org", help="id o nombre; se crea si no existe")
    add.add_argument("--role", choices=[r.value for r in Role], default=Role.MEMBER.value)

    reset = commands.add_parser("set-password", help="cambia la contraseña de una cuenta")
    reset.add_argument("email")
    password_options(reset)

    for name, text in (("disable", "desactiva una cuenta"), ("enable", "reactiva una cuenta")):
        commands.add_parser(name, help=text).add_argument("email")

    commands.add_parser("list", help="lista las cuentas")
    return parser


def run(argv: Sequence[str] | None = None, db: Session | None = None) -> int:
    args = _parser().parse_args(argv)
    session = db or get_sessionmaker()()
    try:
        if args.command == "create":
            password, generated = _read_password(args)
            user = create_user(
                session,
                email=args.email,
                name=args.name,
                password=password,
                locale=args.locale,
                org=args.org,
                role=Role(args.role),
            )
            print(f"Cuenta creada: {user.email} ({user.name})")
            if generated:
                print(f"Contraseña: {password}")
        elif args.command == "add-to-org":
            org = add_to_organization(
                session, _user(session, args.email), args.org, Role(args.role)
            )
            session.commit()
            print(f"{args.email} → {org.name} ({args.role})")
        elif args.command == "set-password":
            password, generated = _read_password(args)
            user = set_password(session, args.email, password)
            print(f"Contraseña cambiada para {user.email}; sesiones cerradas")
            if generated:
                print(f"Contraseña: {password}")
        elif args.command in ("disable", "enable"):
            status = UserStatus.DISABLED if args.command == "disable" else UserStatus.ACTIVE
            user = set_status(session, args.email, status)
            print(f"{user.email}: {status.value}")
        elif args.command == "list":
            for user, orgs in list_users(session):
                memberships = ", ".join(f"{name} ({role.value})" for name, role in orgs)
                mfa = " · MFA" if user.mfa_enabled else ""
                print(
                    f"{user.email:<40} {user.status.value:<9} {user.name}{mfa}"
                    + (f" · {memberships}" if memberships else "")
                )
    except CliError as exc:
        session.rollback()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is None:
            session.close()
    return 0


if __name__ == "__main__":
    sys.exit(run())

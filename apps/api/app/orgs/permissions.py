"""Autorización central por organización.

Regla de aislamiento: quien no es miembro recibe 404 (no se confirma que el recurso exista);
quien es miembro pero no tiene el permiso recibe 403.
"""

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Membership, Organization, Role


class Action(StrEnum):
    ORG_READ = "org.read"
    ORG_UPDATE = "org.update"
    ORG_DELETE = "org.delete"
    MEMBERS_READ = "members.read"
    MEMBERS_MANAGE = "members.manage"
    AUDIT_READ = "audit.read"
    RECORDING_READ = "recording.read"
    RECORDING_CREATE = "recording.create"


_READ = {Action.ORG_READ, Action.MEMBERS_READ, Action.RECORDING_READ}

ROLE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset(_READ),
    Role.MEMBER: frozenset(_READ | {Action.RECORDING_CREATE}),
    Role.ADMIN: frozenset(
        _READ
        | {Action.RECORDING_CREATE, Action.ORG_UPDATE, Action.MEMBERS_MANAGE, Action.AUDIT_READ}
    ),
    Role.OWNER: frozenset(Action),
}

NOT_FOUND = AppError("not_found", "Organización no encontrada", 404)


def can(role: Role, action: Action) -> bool:
    return action in ROLE_PERMISSIONS[role]


def get_membership(db: Session, user_id: uuid.UUID, org_id: uuid.UUID) -> Membership | None:
    return db.scalars(
        select(Membership).where(
            Membership.user_id == user_id, Membership.organization_id == org_id
        )
    ).one_or_none()


def authorize(
    db: Session, user_id: uuid.UUID, org_id: uuid.UUID, action: Action
) -> tuple[Organization, Membership]:
    """Punto único de decisión de acceso a recursos de una organización."""
    membership = get_membership(db, user_id, org_id)
    if membership is None:
        raise NOT_FOUND
    if not can(membership.role, action):
        raise AppError("forbidden", "No tienes permiso para esta acción", 403)
    return db.get_one(Organization, org_id), membership


def can_assign_role(actor_role: Role, target_current: Role | None, target_new: Role | None) -> bool:
    """Un admin gestiona member/viewer/admin, pero no toca propietarios ni crea otros."""
    if actor_role is Role.OWNER:
        return True
    if actor_role is Role.ADMIN:
        return Role.OWNER not in (target_current, target_new)
    return False

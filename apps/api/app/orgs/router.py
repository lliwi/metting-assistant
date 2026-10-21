import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.audit import record_audit_event
from app.auth.dependencies import CurrentUser, DbSession, require_csrf
from app.auth.service import normalize_email
from app.auth.sessions import now
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.core.security import generate_token, hash_token
from app.models import AuditEvent, Membership, Organization, OrganizationInvitation, Role, User
from app.notifications.email import EmailOutbox, build_email, get_email_outbox
from app.orgs.permissions import Action, authorize, can_assign_role
from app.orgs.schemas import (
    AuditEventOut,
    InvitationAccept,
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MemberUpdate,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
)
from app.orgs.service import create_organization

router = APIRouter(prefix="/v1", tags=["organizations"], dependencies=[Depends(require_csrf)])

Client = Annotated[ClientInfo, Depends(get_client_info)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Outbox = Annotated[EmailOutbox, Depends(get_email_outbox)]

INVITATION_TTL = timedelta(days=7)


def _org_out(org: Organization, role: Role) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        name=org.name,
        is_personal=org.is_personal,
        retention_days=org.retention_days,
        role=role,
        created_at=org.created_at,
    )


def _count_owners(db: DbSession, org_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organization_id == org_id, Membership.role == Role.OWNER)
        )
        or 0
    )


# --- Organizaciones ------------------------------------------------------------------------


@router.get("/organizations")
def list_organizations(user: CurrentUser, db: DbSession) -> list[OrganizationOut]:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.is_personal.desc(), Organization.name)
    ).all()
    return [_org_out(org, role) for org, role in rows]


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_org(
    body: OrganizationCreate, user: CurrentUser, db: DbSession, limiter: Limiter, client: Client
) -> OrganizationOut:
    limiter.hit(f"org_create:user:{user.id}", limit=10, window_seconds=3600)
    org = create_organization(db, user, body.name.strip())
    record_audit_event(
        db,
        action="org.create",
        client=client,
        actor_id=user.id,
        organization_id=org.id,
        resource_type="organization",
        resource_id=org.id,
    )
    db.commit()
    db.refresh(org)
    return _org_out(org, Role.OWNER)


@router.get("/organizations/{org_id}")
def get_org(org_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrganizationOut:
    org, membership = authorize(db, user.id, org_id, Action.ORG_READ)
    return _org_out(org, membership.role)


@router.patch("/organizations/{org_id}")
def update_org(
    org_id: uuid.UUID, body: OrganizationUpdate, user: CurrentUser, db: DbSession, client: Client
) -> OrganizationOut:
    org, membership = authorize(db, user.id, org_id, Action.ORG_UPDATE)
    changes: dict[str, object] = {}
    if body.name is not None:
        org.name = changes["name"] = body.name.strip()
    if body.retention_days is not None:
        org.retention_days = changes["retention_days"] = body.retention_days or None
    record_audit_event(
        db,
        action="org.update",
        client=client,
        actor_id=user.id,
        organization_id=org.id,
        resource_type="organization",
        resource_id=org.id,
        metadata=changes,
    )
    db.commit()
    return _org_out(org, membership.role)


# --- Miembros ------------------------------------------------------------------------------


@router.get("/organizations/{org_id}/members")
def list_members(org_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[MemberOut]:
    authorize(db, user.id, org_id, Action.MEMBERS_READ)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == org_id)
        .order_by(User.name)
    ).all()
    return [
        MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at)
        for m, u in rows
    ]


@router.patch("/organizations/{org_id}/members/{member_id}")
def update_member(
    org_id: uuid.UUID,
    member_id: uuid.UUID,
    body: MemberUpdate,
    user: CurrentUser,
    db: DbSession,
    client: Client,
) -> MemberOut:
    org, actor = authorize(db, user.id, org_id, Action.MEMBERS_MANAGE)
    target = db.get(Membership, (member_id, org_id), with_for_update=True)
    if target is None:
        raise AppError("not_found", "Miembro no encontrado", 404)
    if not can_assign_role(actor.role, target.role, body.role):
        raise AppError("forbidden", "No puedes asignar ese rol", 403)
    if target.role is Role.OWNER and body.role is not Role.OWNER and _count_owners(db, org_id) <= 1:
        raise AppError("last_owner", "La organización necesita al menos un propietario", 409)

    previous = target.role
    target.role = body.role
    record_audit_event(
        db,
        action="org.member_role_changed",
        client=client,
        actor_id=user.id,
        organization_id=org.id,
        resource_type="user",
        resource_id=member_id,
        metadata={"from": previous.value, "to": body.role.value},
    )
    db.commit()
    member = db.get_one(User, member_id)
    return MemberOut(
        user_id=member.id,
        email=member.email,
        name=member.name,
        role=target.role,
        joined_at=target.created_at,
    )


@router.delete(
    "/organizations/{org_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_member(
    org_id: uuid.UUID, member_id: uuid.UUID, user: CurrentUser, db: DbSession, client: Client
) -> None:
    leaving = member_id == user.id
    # Cualquiera puede salir de una organización; expulsar a otros requiere permiso.
    org, actor = authorize(
        db, user.id, org_id, Action.ORG_READ if leaving else Action.MEMBERS_MANAGE
    )
    if org.is_personal:
        raise AppError(
            "personal_organization", "El espacio personal no admite cambios de miembros", 409
        )
    target = db.get(Membership, (member_id, org_id), with_for_update=True)
    if target is None:
        raise AppError("not_found", "Miembro no encontrado", 404)
    if not leaving and not can_assign_role(actor.role, target.role, None):
        raise AppError("forbidden", "No puedes quitar a este miembro", 403)
    if target.role is Role.OWNER and _count_owners(db, org_id) <= 1:
        raise AppError("last_owner", "La organización necesita al menos un propietario", 409)

    db.delete(target)
    record_audit_event(
        db,
        action="org.member_removed",
        client=client,
        actor_id=user.id,
        organization_id=org.id,
        resource_type="user",
        resource_id=member_id,
        metadata={"self": leaving},
    )
    db.commit()


# --- Invitaciones --------------------------------------------------------------------------


@router.get("/organizations/{org_id}/invitations")
def list_invitations(org_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[InvitationOut]:
    authorize(db, user.id, org_id, Action.MEMBERS_MANAGE)
    rows = db.scalars(
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == org_id,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
            OrganizationInvitation.expires_at > now(),
        )
        .order_by(OrganizationInvitation.created_at.desc())
    ).all()
    return [
        InvitationOut(
            id=i.id, email=i.email, role=i.role, expires_at=i.expires_at, created_at=i.created_at
        )
        for i in rows
    ]


@router.post("/organizations/{org_id}/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(
    org_id: uuid.UUID,
    body: InvitationCreate,
    user: CurrentUser,
    db: DbSession,
    outbox: Outbox,
    limiter: Limiter,
    client: Client,
) -> InvitationOut:
    org, actor = authorize(db, user.id, org_id, Action.MEMBERS_MANAGE)
    if org.is_personal:
        raise AppError("personal_organization", "El espacio personal no admite miembros", 409)
    if not can_assign_role(actor.role, None, body.role):
        raise AppError("forbidden", "No puedes invitar con ese rol", 403)
    limiter.hit(f"invite:org:{org_id}", limit=50, window_seconds=3600)

    token = generate_token()
    invitation = OrganizationInvitation(
        organization_id=org.id,
        email=normalize_email(body.email),
        role=body.role,
        token_hash=hash_token(token),
        invited_by_id=user.id,
        expires_at=now() + INVITATION_TTL,
    )
    db.add(invitation)
    db.flush()
    base = get_settings().public_app_url.rstrip("/")
    link = f"{base}/{user.locale}/invitations/accept?token={token}"
    outbox.send(
        build_email(
            "invitation",
            to=invitation.email,
            locale=user.locale,
            inviter=user.name,
            organization=org.name,
            role=body.role.value,
            link=link,
        )
    )
    record_audit_event(
        db,
        action="org.invitation_created",
        client=client,
        actor_id=user.id,
        organization_id=org.id,
        resource_type="invitation",
        resource_id=invitation.id,
        metadata={"role": body.role.value},
    )
    db.commit()
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.delete(
    "/organizations/{org_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_invitation(
    org_id: uuid.UUID, invitation_id: uuid.UUID, user: CurrentUser, db: DbSession, client: Client
) -> None:
    authorize(db, user.id, org_id, Action.MEMBERS_MANAGE)
    invitation = db.get(OrganizationInvitation, invitation_id)
    if invitation is None or invitation.organization_id != org_id:
        raise AppError("not_found", "Invitación no encontrada", 404)
    invitation.revoked_at = invitation.revoked_at or now()
    record_audit_event(
        db,
        action="org.invitation_revoked",
        client=client,
        actor_id=user.id,
        organization_id=org_id,
        resource_type="invitation",
        resource_id=invitation_id,
    )
    db.commit()


@router.post("/invitations/accept")
def accept_invitation(
    body: InvitationAccept, user: CurrentUser, db: DbSession, limiter: Limiter, client: Client
) -> OrganizationOut:
    limiter.hit(f"invite_accept:user:{user.id}", limit=10, window_seconds=900)
    invitation = db.scalars(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.token_hash == hash_token(body.token))
        .with_for_update()
    ).one_or_none()
    invalid = AppError("invalid_token", "La invitación no es válida o ha caducado", 400)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now()
    ):
        raise invalid
    # La invitación es para un email concreto y verificado: evita que un enlace reenviado, o
    # una cuenta externa con un email no acreditado, dé acceso a otra persona.
    if invitation.email != normalize_email(user.email) or user.email_verified_at is None:
        raise AppError(
            "invitation_email_mismatch", "Esta invitación es para otra dirección de email", 403
        )

    org = db.get_one(Organization, invitation.organization_id)
    invitation.accepted_at = now()
    existing = db.get(Membership, (user.id, org.id))
    role = existing.role if existing else invitation.role
    if existing is None:
        db.add(Membership(user_id=user.id, organization_id=org.id, role=invitation.role))
    record_audit_event(
        db,
        action="org.invitation_accepted",
        client=client,
        actor_id=user.id,
        organization_id=org.id,
        resource_type="invitation",
        resource_id=invitation.id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise invalid from None
    return _org_out(org, role)


# --- Auditoría -----------------------------------------------------------------------------


@router.get("/organizations/{org_id}/audit-events")
def list_audit_events(
    org_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditEventOut]:
    authorize(db, user.id, org_id, Action.AUDIT_READ)
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == org_id)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(limit)
    ).all()
    return [
        AuditEventOut(
            id=e.id,
            occurred_at=e.occurred_at,
            actor_id=e.actor_id,
            action=e.action,
            result=e.result.value,
            resource_type=e.resource_type,
            resource_id=e.resource_id,
        )
        for e in rows
    ]

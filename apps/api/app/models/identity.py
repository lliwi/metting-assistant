import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPrimaryKey, str_enum


class UserStatus(StrEnum):
    PENDING = "pending"  # email sin verificar
    ACTIVE = "active"
    DISABLED = "disabled"


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)

    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(8), default="es", server_default="es")
    status: Mapped[UserStatus] = mapped_column(
        str_enum(UserStatus, "user_status"), default=UserStatus.PENDING
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # None = cuenta solo con proveedor externo (OIDC).
    password_hash: Mapped[str | None] = mapped_column(String(255))

    mfa_enabled: Mapped[bool] = mapped_column(default=False)
    # Secreto TOTP cifrado con DATA_ENCRYPTION_KEY; nunca en claro.
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Último paso TOTP aceptado: impide reutilizar un código dentro de su ventana.
    totp_last_used_step: Mapped[int | None]

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Organization(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    is_personal: Mapped[bool] = mapped_column(default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # None = sin borrado automático.
    retention_days: Mapped[int | None]

    memberships: Mapped[list["Membership"]] = relationship(back_populates="organization")


class Membership(Base):
    __tablename__ = "memberships"
    # Como mucho una organización preferida por usuario.
    __table_args__ = (
        Index(
            "uq_memberships_preferred_user",
            "user_id",
            unique=True,
            postgresql_where=text("preferred"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[Role] = mapped_column(str_enum(Role, "membership_role"))
    # Preferida: aparece la primera en las listas y es la opción por defecto al grabar.
    preferred: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class UserIdentity(UUIDPrimaryKey, Base):
    """Vínculo con un proveedor externo (Google, Microsoft…) identificado por su `sub`."""

    __tablename__ = "user_identities"
    __table_args__ = (
        Index("uq_user_identities_provider_subject", "provider", "subject", unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrganizationInvitation(UUIDPrimaryKey, Base):
    __tablename__ = "organization_invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[Role] = mapped_column(str_enum(Role, "invitation_role"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped[Organization] = relationship()

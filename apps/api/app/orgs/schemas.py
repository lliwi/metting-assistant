import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models import Role


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # None no cambia; 0 desactiva el borrado automático.
    retention_days: int | None = Field(default=None, ge=0, le=3650)


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    is_personal: bool
    retention_days: int | None
    role: Role
    created_at: datetime


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: Role
    joined_at: datetime


class MemberUpdate(BaseModel):
    role: Role


class InvitationCreate(BaseModel):
    email: EmailStr
    role: Role = Role.MEMBER


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_id: uuid.UUID | None
    action: str
    result: str
    resource_type: str | None
    resource_id: str | None

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import Role

Locale = Literal["es", "ca"]

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


class Password(BaseModel):
    # Longitud mínima en lugar de reglas de composición (NIST SP 800-63B).
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class RegisterRequest(Password):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    locale: Locale = "es"

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("El nombre no puede estar vacío")
        return v


class EmailRequest(BaseModel):
    email: EmailStr


class TokenRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    # Sin mínimo: no revela la política a quien prueba contraseñas.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=16, description="Código TOTP o de recuperación")


class PasswordResetRequest(Password):
    token: str = Field(min_length=16, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class TotpCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    code: str = Field(min_length=6, max_length=16)


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    locale: Locale | None = None


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    is_personal: bool
    role: Role


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    locale: str
    mfa_enabled: bool
    has_password: bool
    memberships: list[MembershipOut]


class LoginResponse(BaseModel):
    status: Literal["authenticated", "mfa_required"]
    user: UserOut | None = None
    mfa_token: str | None = None


class AcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"


class CsrfResponse(BaseModel):
    csrf_token: str


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    current: bool

"""Casos de uso de autenticación con contraseña. El router se encarga de cookies y HTTP.

Las respuestas de registro, reenvío y recuperación son idénticas exista o no la cuenta,
para no permitir enumerar emails registrados.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth import mfa
from app.auth.sessions import create_session, now, revoke_all_sessions
from app.auth.tokens import consume_token, find_valid_token, issue_user_token
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.request_context import ClientInfo
from app.core.security import hash_password, hash_token, password_needs_rehash, verify_password
from app.models import AuditResult, TokenPurpose, User, UserStatus
from app.notifications.email import EmailOutbox, build_email

INVALID_CREDENTIALS = AppError("invalid_credentials", "Email o contraseña incorrectos", 401)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(
        select(User).where(func.lower(User.email) == normalize_email(email))
    ).one_or_none()


def _link(path: str, token: str) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}{path}?token={token}"


def _limit_by_email(
    limiter: RateLimiter, scope: str, email: str, *, limit: int, window: int
) -> None:
    # Se usa el hash para no guardar emails en claro en Redis.
    limiter.hit(
        f"{scope}:email:{hash_token(normalize_email(email))}", limit=limit, window_seconds=window
    )


# --- Login ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginOutcome:
    user: User
    session_token: str | None = None
    mfa_token: str | None = None


def start_session(db: Session, user: User, client: ClientInfo, *, method: str) -> str:
    token = create_session(db, user, client)
    record_audit_event(
        db, action="auth.login", client=client, actor_id=user.id, metadata={"method": method}
    )
    db.commit()
    return token


def login(
    db: Session, limiter: RateLimiter, client: ClientInfo, *, email: str, password: str
) -> LoginOutcome:
    limiter.hit(f"login:ip:{client.ip_pseudonym}", limit=30, window_seconds=60)
    email_key = f"login:email:{hash_token(normalize_email(email))}"
    limiter.hit(email_key, limit=10, window_seconds=900)

    user = find_user_by_email(db, email)
    # verify_password tarda lo mismo aunque el usuario no exista o no tenga contraseña.
    password_ok = verify_password(user.password_hash if user else None, password)

    if user is None or not password_ok or user.status is UserStatus.DISABLED:
        record_audit_event(
            db,
            action="auth.login",
            result=AuditResult.FAILURE,
            client=client,
            actor_id=user.id if user else None,
            metadata={"method": "password", "reason": "invalid_credentials"},
        )
        db.commit()
        raise INVALID_CREDENTIALS

    if user.status is UserStatus.PENDING:
        raise AppError("email_not_verified", "Confirma tu email antes de iniciar sesión", 403)

    limiter.reset(email_key)
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    if user.mfa_enabled:
        mfa_token = issue_user_token(db, user, TokenPurpose.MFA_CHALLENGE)
        db.commit()
        return LoginOutcome(user=user, mfa_token=mfa_token)

    return LoginOutcome(user=user, session_token=start_session(db, user, client, method="password"))


def complete_mfa_login(
    db: Session, limiter: RateLimiter, client: ClientInfo, *, mfa_token: str, code: str
) -> LoginOutcome:
    limiter.hit(f"mfa:token:{hash_token(mfa_token)}", limit=5, window_seconds=300)
    row = find_valid_token(db, mfa_token, TokenPurpose.MFA_CHALLENGE)
    if row is None:
        raise AppError("invalid_token", "La verificación ha caducado; inicia sesión de nuevo", 400)
    user = db.get_one(User, row.user_id)
    limiter.hit(f"mfa:user:{user.id}", limit=10, window_seconds=900)

    method = "totp"
    ok = mfa.verify_totp(user, code)
    if not ok and not code.isdigit():
        method = "recovery_code"
        ok = mfa.use_recovery_code(db, user, code)

    if not ok:
        record_audit_event(
            db,
            action="auth.login",
            result=AuditResult.FAILURE,
            client=client,
            actor_id=user.id,
            metadata={"method": "mfa", "reason": "invalid_code"},
        )
        db.commit()
        raise AppError("invalid_mfa_code", "Código incorrecto", 401)

    consume_token(row)
    return LoginOutcome(user=user, session_token=start_session(db, user, client, method=method))


# --- Contraseña ----------------------------------------------------------------------------


def forgot_password(
    db: Session, outbox: EmailOutbox, limiter: RateLimiter, client: ClientInfo, *, email: str
) -> None:
    limiter.hit(f"forgot:ip:{client.ip_pseudonym}", limit=10, window_seconds=3600)
    _limit_by_email(limiter, "forgot", email, limit=3, window=3600)
    user = find_user_by_email(db, email)
    if user is None or user.status is UserStatus.DISABLED:
        return
    token = issue_user_token(db, user, TokenPurpose.PASSWORD_RESET)
    outbox.send(
        build_email(
            "password_reset",
            to=user.email,
            locale=user.locale,
            name=user.name,
            link=_link(f"/{user.locale}/reset-password", token),
        )
    )
    record_audit_event(db, action="auth.password_reset_requested", client=client, actor_id=user.id)
    db.commit()


def reset_password(db: Session, client: ClientInfo, *, token: str, password: str) -> None:
    row = find_valid_token(db, token, TokenPurpose.PASSWORD_RESET)
    if row is None:
        raise AppError("invalid_token", "El enlace no es válido o ha caducado", 400)
    user = db.get_one(User, row.user_id)
    if user.status is UserStatus.DISABLED:
        raise AppError("invalid_token", "El enlace no es válido o ha caducado", 400)
    consume_token(row)
    user.password_hash = hash_password(password)
    # Recibir el enlace demuestra el control del email.
    if user.status is UserStatus.PENDING:
        user.status = UserStatus.ACTIVE
        user.email_verified_at = now()
    revoke_all_sessions(db, user.id)
    record_audit_event(db, action="auth.password_reset", client=client, actor_id=user.id)
    db.commit()


def change_password(
    db: Session,
    limiter: RateLimiter,
    client: ClientInfo,
    *,
    user: User,
    session_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> None:
    limiter.hit(f"password_change:user:{user.id}", limit=5, window_seconds=900)
    # Cuentas creadas con OIDC no tienen contraseña: solo pueden fijarla vía recuperación.
    if not verify_password(user.password_hash, current_password):
        record_audit_event(
            db,
            action="auth.password_change",
            result=AuditResult.FAILURE,
            client=client,
            actor_id=user.id,
        )
        db.commit()
        raise AppError("invalid_credentials", "La contraseña actual no es correcta", 400)
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user.id, except_id=session_id)
    record_audit_event(db, action="auth.password_change", client=client, actor_id=user.id)
    db.commit()

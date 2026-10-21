import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.request_context import ClientInfo
from app.core.security import generate_token, hash_token, issue_csrf_token
from app.models import User, UserSession, UserStatus

# Evita una escritura en BD por petición: last_seen_at solo se actualiza cada cierto tiempo.
_TOUCH_INTERVAL = timedelta(minutes=5)


def now() -> datetime:
    return datetime.now(UTC)


def create_session(db: Session, user: User, client: ClientInfo) -> str:
    """Crea la sesión y devuelve el token en claro (solo viaja en la cookie)."""
    settings = get_settings()
    token = generate_token()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now() + timedelta(hours=settings.session_absolute_timeout_hours),
            ip_pseudonym=client.ip_pseudonym,
            user_agent=client.user_agent,
        )
    )
    return token


def resolve_session(db: Session, token: str) -> tuple[UserSession, User] | None:
    settings = get_settings()
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    ).one_or_none()
    if row is None:
        return None
    session, user = row

    current = now()
    idle_limit = session.last_seen_at + timedelta(hours=settings.session_idle_timeout_hours)
    if (
        session.revoked_at is not None
        or session.expires_at <= current
        or idle_limit <= current
        or user.status is not UserStatus.ACTIVE
    ):
        return None

    if current - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = current
        db.commit()
    return session, user


def revoke_session(db: Session, session_id: uuid.UUID) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now())
    )


def revoke_all_sessions(
    db: Session, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_id is not None:
        stmt = stmt.where(UserSession.id != except_id)
    db.execute(stmt.values(revoked_at=now()))


def set_session_cookies(response: Response, token: str) -> None:
    settings = get_settings()
    max_age = settings.session_absolute_timeout_hours * 3600
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Se rota el token CSRF al iniciar sesión.
    set_csrf_cookie(response)


def set_csrf_cookie(response: Response) -> str:
    settings = get_settings()
    token = issue_csrf_token()
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.session_absolute_timeout_hours * 3600,
        httponly=False,  # el frontend lo lee para enviarlo en X-CSRF-Token
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return token


def clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name, path="/", secure=settings.cookie_secure, httponly=True
    )

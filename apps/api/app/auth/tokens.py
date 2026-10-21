from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import now
from app.core.security import generate_token, hash_token
from app.models import TokenPurpose, User, UserToken

TOKEN_TTL = {
    TokenPurpose.EMAIL_VERIFICATION: timedelta(hours=24),
    TokenPurpose.PASSWORD_RESET: timedelta(hours=1),
    TokenPurpose.MFA_CHALLENGE: timedelta(minutes=5),
}


def issue_user_token(db: Session, user: User, purpose: TokenPurpose) -> str:
    token = generate_token()
    db.add(
        UserToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(token),
            expires_at=now() + TOKEN_TTL[purpose],
        )
    )
    return token


def find_valid_token(db: Session, token: str, purpose: TokenPurpose) -> UserToken | None:
    """Bloquea la fila: dos peticiones concurrentes no pueden consumir el mismo token."""
    row = db.scalars(
        select(UserToken)
        .where(UserToken.token_hash == hash_token(token), UserToken.purpose == purpose)
        .with_for_update()
    ).one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now():
        return None
    return row


def consume_token(row: UserToken) -> None:
    row.used_at = now()

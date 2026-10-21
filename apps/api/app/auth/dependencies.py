from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.sessions import resolve_session
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import verify_csrf_token
from app.db import get_db
from app.models import User, UserSession

CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: UserSession


def require_csrf(request: Request) -> None:
    """Exige el token CSRF en métodos que modifican estado, también antes de iniciar sesión
    (evita login CSRF)."""
    if request.method in _SAFE_METHODS:
        return
    settings = get_settings()
    if not verify_csrf_token(
        request.cookies.get(settings.csrf_cookie_name), request.headers.get(CSRF_HEADER)
    ):
        raise AppError("csrf_failed", "Token CSRF ausente o no válido", status_code=403)


def get_optional_auth(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> AuthContext | None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        return None
    resolved = resolve_session(db, token)
    if resolved is None:
        return None
    session, user = resolved
    return AuthContext(user=user, session=session)


def get_auth(auth: Annotated[AuthContext | None, Depends(get_optional_auth)]) -> AuthContext:
    if auth is None:
        raise AppError("unauthenticated", "Necesitas iniciar sesión", status_code=401)
    return auth


def get_current_user(auth: Annotated[AuthContext, Depends(get_auth)]) -> User:
    return auth.user


CurrentAuth = Annotated[AuthContext, Depends(get_auth)]
CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]

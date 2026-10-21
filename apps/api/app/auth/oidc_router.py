from functools import lru_cache
from typing import Annotated
from urllib.parse import quote

import httpx
import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.dependencies import DbSession
from app.auth.oidc import (
    STATE_TTL_SECONDS,
    OidcClient,
    OidcError,
    configured_providers,
    resolve_user,
    safe_next_path,
)
from app.auth.service import start_session
from app.auth.sessions import set_session_cookies
from app.auth.tokens import issue_user_token
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter, get_redis
from app.core.request_context import ClientInfo, get_client_info
from app.core.security import hash_token
from app.models import TokenPurpose

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/auth/oidc", tags=["auth"])

STATE_COOKIE = "ma_oidc_state"
DEFAULT_NEXT = "/es"


@lru_cache
def get_oidc_client() -> OidcClient:
    return OidcClient(httpx.Client(), get_redis())


Oidc = Annotated[OidcClient, Depends(get_oidc_client)]
Client = Annotated[ClientInfo, Depends(get_client_info)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


class ProviderOut(BaseModel):
    name: str


def _app_url(path: str) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}{path}"


def _locale_of(path: str) -> str:
    segment = path.split("/")[1] if path.count("/") >= 1 else ""
    return segment if segment in ("es", "ca") else "es"


def _error_redirect(next_path: str, code: str) -> RedirectResponse:
    response = RedirectResponse(
        _app_url(f"/{_locale_of(next_path)}/login?error={quote(code)}"), 303
    )
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.get("/providers")
def list_providers() -> list[ProviderOut]:
    return [ProviderOut(name=name) for name in configured_providers()]


@router.get("/{provider}/start")
def start(
    provider: str, oidc: Oidc, limiter: Limiter, client: Client, next: str | None = None
) -> RedirectResponse:
    config = configured_providers().get(provider)
    if config is None:
        raise AppError("not_found", "Proveedor no disponible", 404)
    limiter.hit(f"oidc_start:ip:{client.ip_pseudonym}", limit=30, window_seconds=300)
    url, state = oidc.authorization_url(config, safe_next_path(next, DEFAULT_NEXT))
    response = RedirectResponse(url, 303)
    # Vincula el state a este navegador: impide inyectar el callback de otra persona.
    response.set_cookie(
        STATE_COOKIE,
        hash_token(state),
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/{provider}/callback")
def callback(
    provider: str,
    request: Request,
    db: DbSession,
    oidc: Oidc,
    client: Client,
    state: Annotated[str, Query(max_length=128)] = "",
    code: Annotated[str, Query(max_length=4096)] = "",
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    config = configured_providers().get(provider)
    cookie = request.cookies.get(STATE_COOKIE)
    data = oidc.pop_state(state) if state else None
    if (
        config is None
        or data is None
        or data["provider"] != provider
        or cookie is None
        or cookie != hash_token(state)
    ):
        return _error_redirect(DEFAULT_NEXT, "invalid_state")
    next_path = data["next"]
    if error or not code:
        return _error_redirect(next_path, "cancelled")

    try:
        claims = oidc.exchange_code(config, code, data["verifier"], data["nonce"])
        user = resolve_user(db, config, claims, client)
    except OidcError as exc:
        db.rollback()
        log.info("oidc_login_failed", provider=provider, reason=exc.code)
        return _error_redirect(next_path, exc.code)
    except httpx.HTTPError:
        db.rollback()
        log.warning("oidc_provider_unreachable", provider=provider)
        return _error_redirect(next_path, "provider_unavailable")

    if user.mfa_enabled:
        mfa_token = issue_user_token(db, user, TokenPurpose.MFA_CHALLENGE)
        db.commit()
        # En el fragmento: no llega a servidores ni a logs de acceso.
        target = f"/{_locale_of(next_path)}/login#mfa_token={mfa_token}&next={quote(next_path)}"
        response = RedirectResponse(_app_url(target), 303)
    else:
        token = start_session(db, user, client, method=provider)
        response = RedirectResponse(_app_url(next_path), 303)
        set_session_cookies(response, token)
    response.delete_cookie(STATE_COOKIE, path="/")
    return response

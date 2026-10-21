"""Login con proveedores OIDC (Google, Microsoft): Authorization Code + PKCE, state y nonce."""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth.service import find_user_by_email, normalize_email
from app.auth.sessions import now
from app.core.config import Settings, get_settings
from app.core.request_context import ClientInfo
from app.models import AuditResult, User, UserIdentity, UserStatus
from app.orgs.service import create_personal_organization

STATE_TTL_SECONDS = 600
_CACHE_TTL_SECONDS = 3600


class OidcError(Exception):
    """Error mostrado al usuario como código en la URL de vuelta (?error=<code>)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Provider:
    name: str
    discovery_url: str
    client_id: str
    client_secret: str
    # Solo se vincula por email a una cuenta existente si el proveedor lo garantiza.
    trusts_email_verified_claim: bool
    scopes: str = "openid email profile"

    def expected_issuer(self, discovery_issuer: str, claims: dict[str, Any]) -> str:
        # Microsoft "common" publica "https://login.microsoftonline.com/{tenantid}/v2.0".
        if "{tenantid}" in discovery_issuer:
            return discovery_issuer.replace("{tenantid}", str(claims.get("tid", "")))
        return discovery_issuer


def configured_providers(settings: Settings | None = None) -> dict[str, Provider]:
    settings = settings or get_settings()
    providers: dict[str, Provider] = {}
    if settings.google_client_id:
        providers["google"] = Provider(
            name="google",
            discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            trusts_email_verified_claim=True,
        )
    if settings.microsoft_client_id:
        providers["microsoft"] = Provider(
            name="microsoft",
            discovery_url=(
                f"https://login.microsoftonline.com/{settings.microsoft_tenant}"
                "/v2.0/.well-known/openid-configuration"
            ),
            client_id=settings.microsoft_client_id,
            client_secret=settings.microsoft_client_secret.get_secret_value(),
            # En Entra ID el claim email lo puede fijar el administrador del tenant.
            trusts_email_verified_claim=False,
        )
    return providers


def callback_url(provider: str) -> str:
    settings = get_settings()
    base = settings.public_app_url.rstrip("/") + settings.root_path
    return f"{base}/v1/auth/oidc/{provider}/callback"


def safe_next_path(value: str | None, default: str) -> str:
    """Solo rutas relativas del propio sitio: evita redirecciones abiertas."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


class OidcClient:
    def __init__(self, http: httpx.Client, redis: Redis) -> None:
        self.http = http
        self.redis = redis
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _get_json(self, url: str) -> dict[str, Any]:
        cached = self._cache.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        response = self.http.get(url, timeout=10)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        self._cache[url] = (time.monotonic() + _CACHE_TTL_SECONDS, data)
        return data

    def discovery(self, provider: Provider) -> dict[str, Any]:
        return self._get_json(provider.discovery_url)

    # --- Inicio --------------------------------------------------------------------------

    def authorization_url(self, provider: Provider, next_path: str) -> tuple[str, str]:
        """Devuelve (URL del proveedor, state). El state se vincula al navegador con una cookie."""
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        self.redis.set(
            f"oidc:state:{state}",
            json.dumps(
                {"provider": provider.name, "nonce": nonce, "verifier": verifier, "next": next_path}
            ),
            ex=STATE_TTL_SECONDS,
        )
        params = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": callback_url(provider.name),
            "scope": provider.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{self.discovery(provider)['authorization_endpoint']}?{urlencode(params)}", state

    # --- Retorno -------------------------------------------------------------------------

    def pop_state(self, state: str) -> dict[str, str] | None:
        raw = self.redis.getdel(f"oidc:state:{state}")
        return json.loads(raw) if raw else None

    def exchange_code(
        self, provider: Provider, code: str, verifier: str, nonce: str
    ) -> dict[str, Any]:
        meta = self.discovery(provider)
        response = self.http.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url(provider.name),
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if response.status_code != 200:
            raise OidcError("token_exchange_failed")
        id_token = response.json().get("id_token")
        if not id_token:
            raise OidcError("token_exchange_failed")
        return self.validate_id_token(provider, id_token, nonce)

    def validate_id_token(self, provider: Provider, id_token: str, nonce: str) -> dict[str, Any]:
        meta = self.discovery(provider)
        try:
            header = jwt.get_unverified_header(id_token)
            jwks = jwt.PyJWKSet.from_dict(self._get_json(meta["jwks_uri"]))
            key = next((k for k in jwks.keys if k.key_id == header.get("kid")), None)
            if key is None:
                # Rotación de claves: se refresca el JWKS una vez.
                self._cache.pop(meta["jwks_uri"], None)
                jwks = jwt.PyJWKSet.from_dict(self._get_json(meta["jwks_uri"]))
                key = next((k for k in jwks.keys if k.key_id == header.get("kid")), None)
            if key is None:
                raise OidcError("invalid_id_token")

            unverified = jwt.decode(id_token, options={"verify_signature": False})
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=provider.client_id,
                issuer=provider.expected_issuer(meta["issuer"], unverified),
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise OidcError("invalid_id_token") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("invalid_id_token")
        return claims


def resolve_user(
    db: Session, provider: Provider, claims: dict[str, Any], client: ClientInfo
) -> User:
    """Busca o crea el usuario de una identidad externa. No abre sesión (puede faltar MFA)."""
    subject = str(claims["sub"])
    email_claim = claims.get("email") or claims.get("preferred_username")
    email = (
        normalize_email(email_claim)
        if isinstance(email_claim, str) and "@" in email_claim
        else None
    )
    email_verified = provider.trusts_email_verified_claim and claims.get("email_verified") is True

    identity = db.scalars(
        select(UserIdentity).where(
            UserIdentity.provider == provider.name, UserIdentity.subject == subject
        )
    ).one_or_none()

    if identity is not None:
        user = db.get_one(User, identity.user_id)
    else:
        if email is None:
            raise OidcError("email_required")
        user_or_none = find_user_by_email(db, email)
        if user_or_none is not None:
            if not email_verified:
                # No se vincula a una cuenta existente sin garantía de que el email es suyo.
                raise OidcError("account_exists")
            user = user_or_none
        else:
            name = str(claims.get("name") or email.split("@")[0])[:200]
            user = User(email=email, name=name, status=UserStatus.ACTIVE, password_hash=None)
            db.add(user)
            db.flush()
            create_personal_organization(db, user)
            record_audit_event(
                db,
                action="auth.register",
                client=client,
                actor_id=user.id,
                resource_type="user",
                resource_id=user.id,
                metadata={"method": provider.name},
            )
        db.add(UserIdentity(user_id=user.id, provider=provider.name, subject=subject, email=email))

    if user.status is UserStatus.DISABLED:
        record_audit_event(
            db,
            action="auth.login",
            result=AuditResult.FAILURE,
            client=client,
            actor_id=user.id,
            metadata={"method": provider.name, "reason": "disabled"},
        )
        db.commit()
        raise OidcError("account_disabled")

    if email_verified and email == normalize_email(user.email):
        # El proveedor acredita el email: completa una verificación pendiente.
        user.email_verified_at = user.email_verified_at or now()
        if user.status is UserStatus.PENDING:
            user.status = UserStatus.ACTIVE
    if user.status is UserStatus.PENDING:
        raise OidcError("email_not_verified")
    return user

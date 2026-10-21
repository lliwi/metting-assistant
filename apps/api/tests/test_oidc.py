import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import fakeredis
import httpx
import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.oidc import OidcClient
from app.auth.oidc_router import STATE_COOKIE, get_oidc_client
from app.models import Membership, User, UserIdentity, UserStatus
from tests.conftest import ApiClient

pytestmark = pytest.mark.db

GOOGLE_ISSUER = "https://accounts.google.com"
MS_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"


class FakeProvider:
    """Simula Google y Microsoft: discovery, JWKS y token endpoint."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key"
        self.claims: dict[str, Any] = {}
        self.nonce_override: str | None = None
        self.issued_codes: dict[str, dict[str, str]] = {}

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid=self.kid, use="sig", alg="RS256")
        return {"keys": [jwk]}

    def id_token(self, *, issuer: str, audience: str, nonce: str) -> str:
        now = int(time.time())
        payload = {
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce_override or nonce,
            **self.claims,
        }
        if "{tenantid}" in issuer:
            payload["iss"] = issuer.replace("{tenantid}", payload.get("tid", ""))
        return jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": self.kid})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "accounts.google.com/.well-known" in url:
            return httpx.Response(200, json=self._discovery("google", GOOGLE_ISSUER))
        if "login.microsoftonline.com" in url and ".well-known" in url:
            return httpx.Response(200, json=self._discovery("microsoft", MS_ISSUER_TEMPLATE))
        if url.endswith("/jwks"):
            return httpx.Response(200, json=self.jwks())
        if url.endswith("/token"):
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            info = self.issued_codes.get(code)
            if info is None or "code_verifier" not in form:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": self.id_token(**info)})
        return httpx.Response(404)

    def _discovery(self, name: str, issuer: str) -> dict[str, str]:
        base = f"https://idp.test/{name}"
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "jwks_uri": f"{base}/jwks",
        }


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def app(app: FastAPI, provider: FakeProvider, redis: fakeredis.FakeRedis) -> Iterator[FastAPI]:
    oidc = OidcClient(httpx.Client(transport=httpx.MockTransport(provider.handler)), redis)
    app.dependency_overrides[get_oidc_client] = lambda: oidc
    yield app


def sign_in(
    client: ApiClient,
    provider: FakeProvider,
    name: str,
    claims: dict[str, Any],
    *,
    next_path: str = "/es/library",
) -> httpx.Response:
    """Recorre start → proveedor → callback como lo haría el navegador."""
    start = client.get(
        f"/v1/auth/oidc/{name}/start", params={"next": next_path}, follow_redirects=False
    )
    assert start.status_code == 303, start.text
    params = parse_qs(urlparse(start.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]

    code = uuid.uuid4().hex
    audience = f"{name}-client-id"
    issuer = GOOGLE_ISSUER if name == "google" else MS_ISSUER_TEMPLATE
    provider.issued_codes[code] = {
        "issuer": issuer,
        "audience": audience,
        "nonce": params["nonce"][0],
    }
    provider.claims = claims
    return client.get(
        f"/v1/auth/oidc/{name}/callback",
        params={"code": code, "state": params["state"][0]},
        follow_redirects=False,
    )


GOOGLE_CLAIMS = {"sub": "g-123", "email": "ana@gmail.com", "email_verified": True, "name": "Ana"}


def test_new_google_user_is_created_and_logged_in(
    client: ApiClient, provider: FakeProvider, db: Session
) -> None:
    res = sign_in(client, provider, "google", GOOGLE_CLAIMS)
    assert res.status_code == 303
    assert res.headers["location"] == "http://testserver/es/library"
    assert client.get("/v1/auth/me").json()["email"] == "ana@gmail.com"

    user = db.scalars(select(User)).one()
    assert user.status is UserStatus.ACTIVE
    assert user.password_hash is None
    assert db.scalars(select(Membership)).one().user_id == user.id

    # Segundo login: misma identidad, sin duplicar el usuario.
    client.cookies.clear()
    sign_in(client, provider, "google", GOOGLE_CLAIMS)
    assert len(db.scalars(select(User)).all()) == 1
    assert len(db.scalars(select(UserIdentity)).all()) == 1


def test_google_links_to_existing_account_with_verified_email(
    client: ApiClient, provider: FakeProvider, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user("ana@gmail.com", verified=False)
    res = sign_in(client, provider, "google", GOOGLE_CLAIMS)
    assert res.headers["location"].endswith("/es/library")
    db.refresh(user)
    assert user.status is UserStatus.ACTIVE  # Google acredita el email
    assert db.scalars(select(UserIdentity)).one().user_id == user.id


def test_microsoft_does_not_link_by_unverified_email(
    client: ApiClient, provider: FakeProvider, make_user: Callable[..., User]
) -> None:
    make_user("ana@contoso.com")
    claims = {"sub": "ms-1", "tid": "tenant-1", "email": "ana@contoso.com", "name": "Impostora"}
    res = sign_in(client, provider, "microsoft", claims)
    assert res.headers["location"] == "http://testserver/es/login?error=account_exists"
    assert client.get("/v1/auth/me").status_code == 401


def test_microsoft_multitenant_issuer_is_validated(
    client: ApiClient, provider: FakeProvider
) -> None:
    claims = {"sub": "ms-2", "tid": "tenant-9", "email": "bob@fabrikam.com", "name": "Bob"}
    res = sign_in(client, provider, "microsoft", claims)
    assert res.headers["location"].endswith("/es/library")


def test_callback_without_state_cookie_is_rejected(
    client: ApiClient, provider: FakeProvider
) -> None:
    """Login CSRF: el callback de otra persona no puede usarse en este navegador."""
    start = client.get("/v1/auth/oidc/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.cookies.delete(STATE_COOKIE)
    res = client.get(
        "/v1/auth/oidc/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert res.headers["location"].endswith("/login?error=invalid_state")


def test_state_cannot_be_replayed(client: ApiClient, provider: FakeProvider) -> None:
    start = client.get("/v1/auth/oidc/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    params = {"code": "unknown", "state": state}
    client.get("/v1/auth/oidc/google/callback", params=params, follow_redirects=False)
    res = client.get("/v1/auth/oidc/google/callback", params=params, follow_redirects=False)
    assert res.headers["location"].endswith("error=invalid_state")


def test_wrong_nonce_is_rejected(client: ApiClient, provider: FakeProvider) -> None:
    provider.nonce_override = "attacker-nonce"
    res = sign_in(client, provider, "google", GOOGLE_CLAIMS)
    assert res.headers["location"].endswith("error=invalid_id_token")


def test_token_signed_with_other_key_is_rejected(client: ApiClient, provider: FakeProvider) -> None:
    # El JWKS publica la clave legítima, pero el token se firma con otra (mismo kid).
    original_jwks = provider.jwks()
    provider.jwks = lambda: original_jwks  # type: ignore[method-assign]
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    res = sign_in(client, provider, "google", GOOGLE_CLAIMS)
    assert res.headers["location"].endswith("error=invalid_id_token")


def test_open_redirect_is_blocked(client: ApiClient, provider: FakeProvider) -> None:
    res = sign_in(client, provider, "google", GOOGLE_CLAIMS, next_path="//evil.example/x")
    assert res.headers["location"] == "http://testserver/es"


def test_mfa_user_must_complete_second_factor(
    client: ApiClient,
    provider: FakeProvider,
    login: Callable[[str], ApiClient],
    make_user: Callable[..., User],
) -> None:
    make_user("ana@gmail.com")
    session = login("ana@gmail.com")
    secret = session.post("/v1/auth/mfa/totp/setup").json()["secret"]
    session.post("/v1/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).now()})

    res = sign_in(client, provider, "google", GOOGLE_CLAIMS)
    location = res.headers["location"]
    assert location.startswith("http://testserver/es/login#mfa_token=")
    assert "ma_session" not in client.cookies
    assert client.get("/v1/auth/me").status_code == 401


def test_unknown_provider_is_404(client: ApiClient) -> None:
    assert client.get("/v1/auth/oidc/github/start", follow_redirects=False).status_code == 404


def test_providers_listed(client: ApiClient) -> None:
    names = {p["name"] for p in client.get("/v1/auth/oidc/providers").json()}
    assert names == {"google", "microsoft"}

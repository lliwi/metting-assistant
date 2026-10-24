from collections.abc import Callable

import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, AuditResult, User, UserStatus
from tests.conftest import PASSWORD, ApiClient, MemoryOutbox

pytestmark = pytest.mark.db

NEW_PASSWORD = "another long passphrase 42"


# --- Sin registro público ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/auth/register", "/v1/auth/verify-email"])
def test_public_registration_is_disabled(client: ApiClient, path: str) -> None:
    res = client.post(path, json={"email": "a@example.com", "password": PASSWORD, "name": "A"})
    assert res.status_code in (404, 405)


def test_pending_account_cannot_log_in(
    client: ApiClient, make_user: Callable[..., User], db: Session
) -> None:
    user = make_user("ana@example.com")
    user.status = UserStatus.PENDING
    db.commit()
    res = client.post("/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "email_not_verified"


# --- CSRF ----------------------------------------------------------------------------------


def test_post_without_csrf_token_is_rejected(client: ApiClient) -> None:
    res = client.request(
        "POST", "/v1/auth/login", json={"email": "a@example.com", "password": "x"}, csrf=False
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "csrf_failed"


def test_forged_csrf_token_is_rejected(client: ApiClient) -> None:
    forged = "nonce.0000"
    client.cookies.set("ma_csrf", forged)
    res = client.post(
        "/v1/auth/login",
        json={"email": "a@example.com", "password": "x"},
        headers={"X-CSRF-Token": forged},
    )
    assert res.json()["error"]["code"] == "csrf_failed"


# --- Login ---------------------------------------------------------------------------------


def test_wrong_password_is_audited(
    client: ApiClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user("ana@example.com")
    res = client.post("/v1/auth/login", json={"email": "ana@example.com", "password": "nope"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_credentials"

    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login")).one()
    assert (event.result, event.actor_id) == (AuditResult.FAILURE, user.id)
    assert event.ip_pseudonym and event.ip_pseudonym != "testclient"


def test_unknown_email_gets_same_error(client: ApiClient) -> None:
    res = client.post("/v1/auth/login", json={"email": "nadie@example.com", "password": "x"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_credentials"


def test_login_is_rate_limited_per_account(
    client: ApiClient, make_user: Callable[..., User]
) -> None:
    make_user("ana@example.com")
    for _ in range(10):
        client.post("/v1/auth/login", json={"email": "ana@example.com", "password": "nope"})
    res = client.post("/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert res.status_code == 429


def test_session_cookie_flags(client: ApiClient, make_user: Callable[..., User]) -> None:
    make_user("ana@example.com")
    res = client.post("/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    cookie = next(h for h in res.headers.get_list("set-cookie") if h.startswith("ma_session="))
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_logout_revokes_session(
    login: Callable[[str], ApiClient], make_user: Callable[..., User]
) -> None:
    make_user("ana@example.com")
    c = login("ana@example.com")
    stolen = c.cookies.get("ma_session")
    assert c.post("/v1/auth/logout").status_code == 204

    c.cookies.set("ma_session", stolen)
    assert c.get("/v1/auth/me").status_code == 401


def test_cannot_revoke_someone_elses_session(
    login: Callable[[str], ApiClient], make_user: Callable[..., User]
) -> None:
    make_user("ana@example.com")
    make_user("bob@example.com")
    ana = login("ana@example.com")
    bob = login("bob@example.com")
    bob_session = bob.get("/v1/auth/sessions").json()[0]["id"]

    assert ana.delete(f"/v1/auth/sessions/{bob_session}").status_code == 404
    assert bob.get("/v1/auth/me").status_code == 200


# --- Contraseña ----------------------------------------------------------------------------


def test_password_reset_revokes_sessions(
    client: ApiClient,
    outbox: MemoryOutbox,
    login: Callable[[str], ApiClient],
    make_user: Callable[..., User],
) -> None:
    make_user("ana@example.com")
    old_session = login("ana@example.com")

    assert (
        client.post("/v1/auth/password/forgot", json={"email": "ana@example.com"}).status_code
        == 202
    )
    token = outbox.token_for("ana@example.com")
    res = client.post("/v1/auth/password/reset", json={"token": token, "password": NEW_PASSWORD})
    assert res.status_code == 200
    assert (
        client.post(
            "/v1/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}
        ).status_code
        == 400
    )

    assert old_session.get("/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    login("ana@example.com", NEW_PASSWORD)


def test_forgot_password_unknown_email_is_silent(client: ApiClient, outbox: MemoryOutbox) -> None:
    res = client.post("/v1/auth/password/forgot", json={"email": "nadie@example.com"})
    assert res.status_code == 202
    assert outbox.sent == []


def test_change_password_keeps_current_session_only(
    login: Callable[[str], ApiClient], make_user: Callable[..., User]
) -> None:
    make_user("ana@example.com")
    other = login("ana@example.com")
    current = login("ana@example.com")

    res = current.post(
        "/v1/auth/password/change",
        json={"current_password": "wrong", "new_password": NEW_PASSWORD},
    )
    assert res.status_code == 400
    res = current.post(
        "/v1/auth/password/change",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert res.status_code == 200
    assert current.get("/v1/auth/me").status_code == 200
    assert other.get("/v1/auth/me").status_code == 401


# --- MFA -----------------------------------------------------------------------------------


def enable_mfa(c: ApiClient) -> tuple[pyotp.TOTP, list[str]]:
    secret = c.post("/v1/auth/mfa/totp/setup").json()["secret"]
    totp = pyotp.TOTP(secret)
    res = c.post("/v1/auth/mfa/totp/confirm", json={"code": totp.now()})
    assert res.status_code == 200, res.text
    return totp, res.json()["recovery_codes"]


def test_mfa_login_flow(
    client: ApiClient, login: Callable[[str], ApiClient], make_user: Callable[..., User]
) -> None:
    make_user("ana@example.com")
    totp, recovery = enable_mfa(login("ana@example.com"))
    assert len(recovery) == 10

    res = client.post("/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    body = res.json()
    assert body["status"] == "mfa_required"
    assert "ma_session" not in client.cookies
    assert client.get("/v1/auth/me").status_code == 401

    bad = client.post("/v1/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": "000000"})
    assert bad.status_code == 401

    # El código de confirmación ya se usó: no puede reutilizarse (protección de replay).
    res = client.post(
        "/v1/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": totp.now()}
    )
    assert res.json()["error"]["code"] == "invalid_mfa_code"

    # Código de recuperación: válido una sola vez.
    res = client.post(
        "/v1/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": recovery[0]}
    )
    assert res.status_code == 200, res.text
    assert client.get("/v1/auth/me").json()["mfa_enabled"] is True

    again = client.post("/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    res = client.post(
        "/v1/auth/login/mfa", json={"mfa_token": again.json()["mfa_token"], "code": recovery[0]}
    )
    assert res.status_code == 401


def test_mfa_secret_is_encrypted_at_rest(
    db: Session, login: Callable[[str], ApiClient], make_user: Callable[..., User]
) -> None:
    user = make_user("ana@example.com")
    c = login("ana@example.com")
    secret = c.post("/v1/auth/mfa/totp/setup").json()["secret"]
    db.refresh(user)
    assert user.totp_secret_encrypted
    assert secret.encode() not in user.totp_secret_encrypted


def test_disable_mfa_requires_password_and_code(
    login: Callable[[str], ApiClient], make_user: Callable[..., User]
) -> None:
    make_user("ana@example.com")
    c = login("ana@example.com")
    _, recovery = enable_mfa(c)

    res = c.post("/v1/auth/mfa/totp/disable", json={"password": "wrong", "code": recovery[0]})
    assert res.status_code == 400
    res = c.post("/v1/auth/mfa/totp/disable", json={"password": PASSWORD, "code": recovery[1]})
    assert res.status_code == 200
    assert c.get("/v1/auth/me").json()["mfa_enabled"] is False

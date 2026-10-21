import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth import mfa, service
from app.auth.dependencies import CurrentAuth, CurrentUser, DbSession, require_csrf
from app.auth.schemas import (
    AcceptedResponse,
    CsrfResponse,
    EmailRequest,
    LoginRequest,
    LoginResponse,
    MembershipOut,
    MfaDisableRequest,
    MfaLoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    RecoveryCodesResponse,
    RegisterRequest,
    SessionOut,
    TokenRequest,
    TotpCodeRequest,
    TotpSetupResponse,
    UpdateMeRequest,
    UserOut,
)
from app.auth.sessions import (
    clear_session_cookies,
    now,
    revoke_session,
    set_csrf_cookie,
    set_session_cookies,
)
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.request_context import ClientInfo, get_client_info
from app.core.security import verify_password
from app.models import AuditResult, Membership, Organization, User, UserSession
from app.notifications.email import EmailOutbox, get_email_outbox

router = APIRouter(prefix="/v1/auth", tags=["auth"], dependencies=[Depends(require_csrf)])

Client = Annotated[ClientInfo, Depends(get_client_info)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Outbox = Annotated[EmailOutbox, Depends(get_email_outbox)]


def user_out(db: Session, user: User) -> UserOut:
    rows = db.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.is_personal.desc(), Organization.name)
    ).all()
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        locale=user.locale,
        mfa_enabled=user.mfa_enabled,
        has_password=user.password_hash is not None,
        memberships=[
            MembershipOut(
                organization_id=org.id,
                organization_name=org.name,
                is_personal=org.is_personal,
                role=m.role,
            )
            for m, org in rows
        ],
    )


@router.get("/csrf")
def get_csrf_token(response: Response) -> CsrfResponse:
    """Emite el token CSRF (cookie legible + cuerpo). Llamar antes de la primera petición POST."""
    return CsrfResponse(csrf_token=set_csrf_cookie(response))


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(
    body: RegisterRequest, db: DbSession, outbox: Outbox, limiter: Limiter, client: Client
) -> AcceptedResponse:
    service.register(
        db,
        outbox,
        limiter,
        client,
        email=body.email,
        password=body.password,
        name=body.name,
        locale=body.locale,
    )
    return AcceptedResponse()


@router.post("/verify-email")
def verify_email(body: TokenRequest, db: DbSession, client: Client) -> AcceptedResponse:
    service.verify_email(db, client, token=body.token)
    return AcceptedResponse()


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(
    body: EmailRequest, db: DbSession, outbox: Outbox, limiter: Limiter, client: Client
) -> AcceptedResponse:
    service.resend_verification(db, outbox, limiter, client, email=body.email)
    return AcceptedResponse()


@router.post("/login")
def login(
    body: LoginRequest, response: Response, db: DbSession, limiter: Limiter, client: Client
) -> LoginResponse:
    outcome = service.login(db, limiter, client, email=body.email, password=body.password)
    if outcome.mfa_token:
        return LoginResponse(status="mfa_required", mfa_token=outcome.mfa_token)
    assert outcome.session_token
    set_session_cookies(response, outcome.session_token)
    return LoginResponse(status="authenticated", user=user_out(db, outcome.user))


@router.post("/login/mfa")
def login_mfa(
    body: MfaLoginRequest, response: Response, db: DbSession, limiter: Limiter, client: Client
) -> LoginResponse:
    outcome = service.complete_mfa_login(
        db, limiter, client, mfa_token=body.mfa_token, code=body.code
    )
    assert outcome.session_token
    set_session_cookies(response, outcome.session_token)
    return LoginResponse(status="authenticated", user=user_out(db, outcome.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: CurrentAuth, response: Response, db: DbSession, client: Client) -> None:
    revoke_session(db, auth.session.id)
    record_audit_event(db, action="auth.logout", client=client, actor_id=auth.user.id)
    db.commit()
    clear_session_cookies(response)


@router.get("/me")
def me(user: CurrentUser, db: DbSession) -> UserOut:
    return user_out(db, user)


@router.patch("/me")
def update_me(body: UpdateMeRequest, user: CurrentUser, db: DbSession) -> UserOut:
    if body.name is not None:
        user.name = body.name.strip()
    if body.locale is not None:
        user.locale = body.locale
    db.commit()
    return user_out(db, user)


# --- Contraseña ----------------------------------------------------------------------------


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    body: EmailRequest, db: DbSession, outbox: Outbox, limiter: Limiter, client: Client
) -> AcceptedResponse:
    service.forgot_password(db, outbox, limiter, client, email=body.email)
    return AcceptedResponse()


@router.post("/password/reset")
def reset_password(body: PasswordResetRequest, db: DbSession, client: Client) -> AcceptedResponse:
    service.reset_password(db, client, token=body.token, password=body.password)
    return AcceptedResponse()


@router.post("/password/change")
def change_password(
    body: PasswordChangeRequest, auth: CurrentAuth, db: DbSession, limiter: Limiter, client: Client
) -> AcceptedResponse:
    service.change_password(
        db,
        limiter,
        client,
        user=auth.user,
        session_id=auth.session.id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return AcceptedResponse()


# --- Sesiones ------------------------------------------------------------------------------


@router.get("/sessions")
def list_sessions(auth: CurrentAuth, db: DbSession) -> list[SessionOut]:
    rows = db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == auth.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now(),
        )
        .order_by(UserSession.last_seen_at.desc())
    ).all()
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            user_agent=s.user_agent,
            current=s.id == auth.session.id,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: uuid.UUID, auth: CurrentAuth, db: DbSession, client: Client) -> None:
    target = db.get(UserSession, session_id)
    # 404 también si la sesión es de otro usuario: no se confirma que exista.
    if target is None or target.user_id != auth.user.id:
        raise AppError("not_found", "Sesión no encontrada", 404)
    revoke_session(db, target.id)
    record_audit_event(
        db,
        action="auth.session_revoked",
        client=client,
        actor_id=auth.user.id,
        resource_type="session",
        resource_id=target.id,
    )
    db.commit()


# --- MFA -----------------------------------------------------------------------------------


@router.post("/mfa/totp/setup")
def totp_setup(user: CurrentUser, db: DbSession) -> TotpSetupResponse:
    if user.mfa_enabled:
        raise AppError("mfa_already_enabled", "La verificación en dos pasos ya está activa", 409)
    secret, uri = mfa.start_totp_setup(user)
    db.commit()
    return TotpSetupResponse(secret=secret, otpauth_uri=uri)


@router.post("/mfa/totp/confirm")
def totp_confirm(
    body: TotpCodeRequest, user: CurrentUser, db: DbSession, limiter: Limiter, client: Client
) -> RecoveryCodesResponse:
    limiter.hit(f"mfa_confirm:user:{user.id}", limit=10, window_seconds=900)
    if user.mfa_enabled:
        raise AppError("mfa_already_enabled", "La verificación en dos pasos ya está activa", 409)
    if user.totp_secret_encrypted is None:
        raise AppError("mfa_setup_required", "Inicia la configuración primero", 409)
    if not mfa.verify_totp(user, body.code):
        db.commit()  # conserva totp_last_used_step si cambió
        raise AppError("invalid_mfa_code", "Código incorrecto", 400)
    user.mfa_enabled = True
    codes = mfa.regenerate_recovery_codes(db, user)
    record_audit_event(db, action="auth.mfa_enabled", client=client, actor_id=user.id)
    db.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/mfa/recovery-codes")
def regenerate_recovery_codes(
    body: TotpCodeRequest, user: CurrentUser, db: DbSession, limiter: Limiter, client: Client
) -> RecoveryCodesResponse:
    limiter.hit(f"mfa_confirm:user:{user.id}", limit=10, window_seconds=900)
    if not user.mfa_enabled or not mfa.verify_totp(user, body.code):
        db.commit()
        raise AppError("invalid_mfa_code", "Código incorrecto", 400)
    codes = mfa.regenerate_recovery_codes(db, user)
    record_audit_event(db, action="auth.mfa_recovery_regenerated", client=client, actor_id=user.id)
    db.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/mfa/totp/disable")
def totp_disable(
    body: MfaDisableRequest, user: CurrentUser, db: DbSession, limiter: Limiter, client: Client
) -> AcceptedResponse:
    limiter.hit(f"mfa_disable:user:{user.id}", limit=5, window_seconds=900)
    if not user.mfa_enabled:
        raise AppError("mfa_not_enabled", "La verificación en dos pasos no está activa", 409)
    password_ok = user.password_hash is None or verify_password(user.password_hash, body.password)
    code_ok = mfa.verify_totp(user, body.code) or mfa.use_recovery_code(db, user, body.code)
    if not (password_ok and code_ok):
        record_audit_event(
            db,
            action="auth.mfa_disabled",
            result=AuditResult.FAILURE,
            client=client,
            actor_id=user.id,
        )
        db.commit()
        raise AppError("invalid_credentials", "Contraseña o código incorrectos", 400)
    mfa.disable_mfa(db, user)
    record_audit_event(db, action="auth.mfa_disabled", client=client, actor_id=user.id)
    db.commit()
    return AcceptedResponse()

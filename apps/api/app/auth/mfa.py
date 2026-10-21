import hmac
import secrets

import pyotp
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.sessions import now
from app.core.security import decrypt, encrypt, hash_token
from app.models import MfaRecoveryCode, User

ISSUER = "Metting Agent"
RECOVERY_CODE_COUNT = 10
# Acepta el código anterior y el siguiente por desfase de reloj (±30 s).
_VALID_WINDOW = 1


def start_totp_setup(user: User) -> tuple[str, str]:
    """Genera un secreto nuevo (pendiente de confirmar). Devuelve (secreto, URI otpauth)."""
    secret = pyotp.random_base32()
    user.totp_secret_encrypted = encrypt(secret)
    user.totp_last_used_step = None
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return secret, uri


def verify_totp(user: User, code: str) -> bool:
    """Valida el código y registra su paso para que no pueda reutilizarse."""
    if user.totp_secret_encrypted is None or not code.isdigit():
        return False
    totp = pyotp.TOTP(decrypt(user.totp_secret_encrypted))
    current = totp.timecode(now())
    for step in range(current - _VALID_WINDOW, current + _VALID_WINDOW + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            if user.totp_last_used_step is not None and step <= user.totp_last_used_step:
                return False
            user.totp_last_used_step = step
            return True
    return False


def _normalize_recovery_code(code: str) -> str:
    return code.replace("-", "").replace(" ", "").lower()


def regenerate_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)  # 10 caracteres hex, 40 bits
        codes.append(f"{raw[:5]}-{raw[5:]}")
        db.add(MfaRecoveryCode(user_id=user.id, code_hash=hash_token(raw)))
    return codes


def use_recovery_code(db: Session, user: User, code: str) -> bool:
    row = db.scalars(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_hash == hash_token(_normalize_recovery_code(code)),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        return False
    row.used_at = now()
    return True


def disable_mfa(db: Session, user: User) -> None:
    user.mfa_enabled = False
    user.totp_secret_encrypted = None
    user.totp_last_used_step = None
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))

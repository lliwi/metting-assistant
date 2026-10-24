"""Primitivas criptográficas: contraseñas, tokens opacos, CSRF, cifrado y pseudonimización."""

import hashlib
import hmac
import secrets
import threading
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

# Parámetros por defecto de argon2-cffi (Argon2id, RFC 9106 perfil "low memory"): cada operación
# reserva 64 MiB. Sin límite, una ráfaga de logins (o un ataque desde muchas IPs) agota la
# memoria de la API; con el semáforo, como mucho ARGON2_MAX_CONCURRENCY por proceso y el resto
# espera su turno.
_hasher = PasswordHasher()
_argon2_slots = threading.BoundedSemaphore(get_settings().argon2_max_concurrency)
# Hash de referencia para igualar el tiempo de respuesta cuando el usuario no existe.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class MissingSecretError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    with _argon2_slots:
        return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Comprueba la contraseña en tiempo similar exista o no el hash."""
    try:
        with _argon2_slots:
            valid = _hasher.verify(password_hash or _DUMMY_HASH, password)
        return valid and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Los tokens opacos son de alta entropía: basta SHA-256 (sin sal) para buscarlos en BD."""
    return hashlib.sha256(token.encode()).hexdigest()


def _master_key() -> bytes:
    key = get_settings().app_secret_key.get_secret_value()
    if not key:
        raise MissingSecretError("APP_SECRET_KEY no está configurada")
    return key.encode()


def derive_key(label: str) -> bytes:
    """Subclave independiente por uso, para no reutilizar la clave maestra directamente."""
    return hmac.new(_master_key(), label.encode(), hashlib.sha256).digest()


def pseudonymize_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hmac.new(derive_key("ip-pseudonym"), ip.encode(), hashlib.sha256).hexdigest()


# --- CSRF: double-submit firmado ---------------------------------------------------------
# El token es "<aleatorio>.<hmac>". Una cookie inyectada desde otro subdominio no lleva una
# firma válida, así que no basta con fijar la cookie para falsificar la cabecera.


def issue_csrf_token() -> str:
    nonce = secrets.token_urlsafe(24)
    sig = hmac.new(derive_key("csrf"), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def verify_csrf_token(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    if not hmac.compare_digest(cookie_value, header_value):
        return False
    nonce, _, sig = cookie_value.partition(".")
    expected = hmac.new(derive_key("csrf"), nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# --- Cifrado de datos en BD -----------------------------------------------------------------


@lru_cache
def _fernet(key: str) -> Fernet:
    return Fernet(key.encode())


def _current_fernet() -> Fernet:
    key = get_settings().data_encryption_key.get_secret_value()
    if not key:
        raise MissingSecretError("DATA_ENCRYPTION_KEY no está configurada")
    return _fernet(key)


def encrypt(value: str) -> bytes:
    return _current_fernet().encrypt(value.encode())


def decrypt(value: bytes) -> str:
    try:
        return _current_fernet().decrypt(value).decode()
    except InvalidToken as exc:
        raise ValueError("No se pudo descifrar el valor") from exc

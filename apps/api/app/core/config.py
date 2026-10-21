from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    # Prefijo bajo el que el reverse proxy publica la API (p. ej. "/api").
    root_path: str = ""

    database_url: str = "postgresql+psycopg://metting:metting@localhost:5432/metting"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "http://localhost:3900"
    s3_region: str = "garage"
    s3_bucket: str = "recordings"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    # Cifrado en reposo en el proveedor S3: "AES256" o "aws:kms". Vacío en Garage, que no
    # implementa SSE-S3: en on-premise el cifrado va en el volumen (LUKS / disco cifrado).
    s3_server_side_encryption: str = ""

    # Captura por segmentos.
    segment_max_bytes: int = 10 * 1024 * 1024
    segment_max_duration_ms: int = 120_000
    recording_max_duration_minutes: int = 240

    # URL pública de la web: enlaces en emails y redirecciones tras OIDC.
    public_app_url: str = "http://localhost:8080"

    # Clave maestra para firmar (CSRF) y pseudonimizar IPs. Se derivan subclaves por uso.
    app_secret_key: SecretStr = SecretStr("")
    # Clave Fernet (base64 urlsafe, 32 bytes) para datos cifrados en BD, p. ej. secretos TOTP.
    data_encryption_key: SecretStr = SecretStr("")
    # Reservada para tokens de la API pública (Fase 2).
    jwt_signing_key: SecretStr = SecretStr("")

    session_cookie_name: str = "ma_session"
    csrf_cookie_name: str = "ma_csrf"
    cookie_secure: bool = True
    session_idle_timeout_hours: int = 24 * 7
    session_absolute_timeout_hours: int = 24 * 30
    # Solo detrás de nginx: confía en X-Real-IP para la IP del cliente.
    trust_proxy_headers: bool = False

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    email_from: str = "Metting Agent <no-reply@metting.local>"

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    microsoft_client_id: str = ""
    microsoft_client_secret: SecretStr = SecretStr("")
    # "common" admite cuentas de cualquier tenant y personales; un GUID restringe a un tenant.
    microsoft_tenant: str = "common"

    asr_provider: str = "whisper"
    llm_provider: str = "managed_provider"

    cors_origins: list[str] = Field(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @model_validator(mode="after")
    def _require_secrets_outside_dev(self) -> Self:
        if self.environment is Environment.DEVELOPMENT:
            return self
        missing = [
            name
            for name in ("app_secret_key", "data_encryption_key")
            if not getattr(self, name).get_secret_value()
        ]
        if missing:
            raise ValueError(f"Faltan secretos obligatorios: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

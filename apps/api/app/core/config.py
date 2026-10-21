from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
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

    jwt_signing_key: SecretStr = SecretStr("")
    oidc_issuer: str = ""

    asr_provider: str = "whisper"
    llm_provider: str = "managed_provider"

    cors_origins: list[str] = Field(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()

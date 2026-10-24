import tempfile
from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Motor de transcripción: "local" (Whisper + pyannote en el worker; el audio no sale) u
# "openai" (gpt-4o-transcribe-diarize, o el modelo de transcripción sin hablantes).
TranscriptionEngine = Literal["local", "openai"]


class ModelPrice(BaseModel):
    """Precio en USD de un modelo (AI_PRICES). Se suman las partes que apliquen."""

    input_per_mtok: float = 0  # por millón de tokens de entrada
    output_per_mtok: float = 0  # por millón de tokens de salida
    per_audio_minute: float = 0  # por minuto de audio (transcripción)


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

    # Importación de archivos (subidos en bloques por el mismo endpoint de segmentos).
    import_max_bytes: int = 2 * 1024**3
    # Disco de trabajo del worker para unir bloques y extraer audio (no tmpfs: puede ser grande).
    scratch_dir: str = Field(default_factory=tempfile.gettempdir)

    # Antivirus de archivos importados. "clamd" en producción; "disabled" solo en desarrollo.
    antivirus_mode: str = "clamd"
    clamd_host: str = "clamav"
    clamd_port: int = 3310

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

    # --- Transcripción (ASR) ---------------------------------------------------------------
    # "faster_whisper": local en el worker (el audio no sale). "openai": API de OpenAI o un
    # servidor compatible (OPENAI_BASE_URL).
    asr_provider: Literal["faster_whisper", "openai"] = "faster_whisper"
    whisper_model: str = "small"  # tiny | base | small | medium | large-v3 | turbo
    whisper_device: str = "cpu"  # cpu | cuda
    whisper_compute_type: str = "int8"  # int8 (CPU) | float16 (GPU)
    whisper_models_dir: str = Field(default_factory=lambda: f"{tempfile.gettempdir()}/whisper")
    whisper_beam_size: int = 5

    # --- Identificación de hablantes (diarización) -----------------------------------------
    # none: sin hablantes. openai: gpt-4o-transcribe-diarize transcribe e identifica a la vez
    # (sustituye al ASR; el audio va a OpenAI). pyannote: local, tras Whisper (requiere
    # HF_TOKEN con acceso al modelo y la imagen con GPU).
    diarization_provider: Literal["none", "openai", "pyannote"] = "none"
    openai_diarize_model: str = "gpt-4o-transcribe-diarize"
    pyannote_model: str = "pyannote/speaker-diarization-community-1"
    hf_token: SecretStr = SecretStr("")

    # --- Modelos de lenguaje (resúmenes, actas, tareas) ------------------------------------
    llm_provider: Literal["ollama", "openai"] = "ollama"
    # Generar resumen y acta automáticamente al terminar cada transcripción. Por defecto no:
    # se generan bajo demanda, documento a documento (consumo de LLM solo cuando se pide).
    ai_auto_generate: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    # Ollama usa 4096 tokens por defecto; una hora de reunión ronda los 15 000.
    ollama_num_ctx: int = 16384
    ollama_timeout_seconds: int = 600
    # Cuánto mantiene Ollama el modelo en memoria tras responder. "0" lo descarga al momento:
    # útil si comparte GPU con Whisper (si no, la siguiente transcripción se queda sin VRAM).
    ollama_keep_alive: str = "5m"

    # --- OpenAI (opcional: ASR y/o LLM) -----------------------------------------------------
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_transcription_model: str = "whisper-1"
    openai_llm_model: str = ""

    # --- Coste estimado ---------------------------------------------------------------------
    # JSON {modelo: {input_per_mtok, output_per_mtok, per_audio_minute}} en USD. Consulta los
    # precios vigentes del proveedor; sin entrada, se registra el consumo sin coste.
    ai_prices: dict[str, ModelPrice] = Field(default_factory=dict)

    # Operaciones Argon2 simultáneas por proceso (64 MiB cada una).
    argon2_max_concurrency: int = Field(default=2, ge=1, le=32)

    cors_origins: list[str] = Field(default_factory=list)

    @property
    def default_engine(self) -> TranscriptionEngine:
        """Motor con el que se transcribe si la grabación no pide otro."""
        if "openai" in (self.asr_provider, self.diarization_provider):
            return "openai"
        return "local"

    @property
    def available_engines(self) -> list[TranscriptionEngine]:
        """local: Whisper en el worker (+ pyannote si hay HF_TOKEN). openai: requiere clave."""
        engines: list[TranscriptionEngine] = ["local"]
        if self.openai_api_key.get_secret_value():
            engines.append("openai")
        return engines

    @property
    def local_diarization(self) -> bool:
        """pyannote en el motor local: con HF_TOKEN, salvo que se desactive la diarización."""
        return self.diarization_provider != "none" and bool(self.hf_token.get_secret_value())

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
        if "openai" in (self.asr_provider, self.llm_provider, self.diarization_provider) and not (
            self.openai_api_key.get_secret_value()
        ):
            missing.append("openai_api_key")
        if self.llm_provider == "openai" and not self.openai_llm_model:
            missing.append("openai_llm_model")
        if self.diarization_provider == "pyannote" and not self.hf_token.get_secret_value():
            missing.append("hf_token")
        if missing:
            raise ValueError(f"Faltan secretos obligatorios: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

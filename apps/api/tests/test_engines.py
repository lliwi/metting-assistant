"""Modo híbrido: motor de transcripción local (Whisper + pyannote) u OpenAI por grabación."""

import uuid
from pathlib import Path

import fakeredis
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.ai.asr import AsrError, FasterWhisperProvider, OpenAIAsrProvider, get_asr_provider
from app.ai.diarization import PyannoteDiarizer, get_diarizer
from app.ai.speakers import SpeakerTurn
from app.core.config import Settings, get_settings
from app.media.pipeline import process_recording
from app.models import Recording
from tests.conftest import ApiClient, MemoryStorage
from tests.test_pipeline import CleanScanner, FakeAsr, Queue, ana, queue, record, run  # noqa: F401 - fixtures


def settings(**overrides: object) -> Settings:
    """Configuración aislada del entorno (variables y .env)."""
    base: dict[str, object] = {
        "environment": "development",  # sin la validación de secretos obligatorios
        "asr_provider": "faster_whisper",
        "llm_provider": "ollama",
        "diarization_provider": "none",
        "openai_api_key": SecretStr(""),
        "hf_token": SecretStr(""),
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


def test_default_engine_follows_configuration() -> None:
    assert settings().default_engine == "local"
    assert settings(diarization_provider="openai").default_engine == "openai"
    assert settings(asr_provider="openai").default_engine == "openai"


def test_openai_engine_requires_api_key() -> None:
    assert settings().available_engines == ["local"]
    assert settings(openai_api_key=SecretStr("sk-x")).available_engines == ["local", "openai"]


def test_local_diarization_needs_token() -> None:
    assert not settings(diarization_provider="pyannote").local_diarization
    assert settings(diarization_provider="openai", hf_token=SecretStr("hf_x")).local_diarization
    assert not settings(diarization_provider="none", hf_token=SecretStr("hf_x")).local_diarization


def test_factories_pick_the_requested_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    current = get_settings()
    for name, value in {
        "diarization_provider": "openai",
        "openai_api_key": SecretStr("sk-x"),
        "hf_token": SecretStr("hf_x"),
    }.items():
        monkeypatch.setattr(current, name, value)

    # Por defecto (configuración): OpenAI, que ya identifica hablantes.
    assert isinstance(get_asr_provider(), OpenAIAsrProvider)
    assert get_diarizer() is None
    # Pedido por la grabación: local, con pyannote tras Whisper.
    assert isinstance(get_asr_provider("local"), FasterWhisperProvider)
    assert isinstance(get_diarizer("local"), PyannoteDiarizer)

    monkeypatch.setattr(current, "hf_token", SecretStr(""))
    assert get_diarizer("local") is None


@pytest.mark.db
def test_reprocess_with_another_engine(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-x"))
    engines = ana.get("/v1/transcription-engines").json()
    assert engines["available"] == ["local", "openai"]

    rec = record(ana, tmp_path)
    run(db, storage, redis, rec["id"], FakeAsr())
    res = ana.post(f"/v1/recordings/{rec['id']}/reprocess", json={"engine": "openai"})
    assert res.status_code == 202, res.text
    assert res.json()["transcription_engine"] == "openai"
    db.expire_all()
    assert db.get_one(Recording, uuid.UUID(rec["id"])).transcription_engine == "openai"


@pytest.mark.db
def test_unavailable_engine_is_rejected(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(""))
    rec = record(ana, tmp_path)
    run(db, storage, redis, rec["id"], FakeAsr())
    res = ana.post(f"/v1/recordings/{rec['id']}/reprocess", json={"engine": "openai"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "engine_unavailable"


class BrokenDiarizer:
    def __init__(self, transient: bool) -> None:
        self.transient = transient

    def diarize(self, path: Path) -> list[SpeakerTurn]:
        raise AsrError("403: modelo sin acceso", transient=self.transient)


@pytest.mark.db
def test_permanent_diarization_failure_keeps_transcript(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    rid = uuid.UUID(rec["id"])
    process_recording(
        db, storage, CleanScanner(), FakeAsr(), redis, rid,  # type: ignore[arg-type]
        diarizer=BrokenDiarizer(transient=False),
    )  # fmt: skip
    transcript = ana.get(f"/v1/recordings/{rec['id']}/transcript").json()
    assert transcript["segments"]
    assert all(s["speaker"] is None for s in transcript["segments"])


@pytest.mark.db
def test_transient_diarization_failure_is_retried(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    rec = record(ana, tmp_path)
    with pytest.raises(AsrError):
        process_recording(
            db, storage, CleanScanner(), FakeAsr(), redis, uuid.UUID(rec["id"]),  # type: ignore[arg-type]
            diarizer=BrokenDiarizer(transient=True),
        )  # fmt: skip

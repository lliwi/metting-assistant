"""Modo híbrido: motor de transcripción local (Whisper + pyannote) u OpenAI por grabación."""

import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis
import httpx
import openai
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.ai import asr as asr_module
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


@pytest.mark.parametrize(
    ("size", "minutes", "fits"),
    [
        (1024, 10, True),
        (1024, 20, True),
        # Cabe en 24 MB (64 kbit/s), pero supera la duración que admiten los modelos gpt-4o.
        (20 * 1024 * 1024, 45, False),
        (25 * 1024 * 1024, 10, False),
    ],
)
def test_openai_splits_by_size_or_duration(
    size: int, minutes: int, fits: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "audio.m4a"
    with audio.open("wb") as f:
        f.truncate(size)
    monkeypatch.setattr(
        asr_module, "probe", lambda _: SimpleNamespace(duration_ms=minutes * 60_000)
    )
    assert asr_module._fits_in_one_request(audio) is fits


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


def test_openai_waits_long_enough_for_a_full_piece(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regresión: a ~0,5 s por segundo de audio, un trozo de 20 min tarda unos 600 s, justo el
    límite que había; la petición se cortaba y la grabación nunca terminaba."""
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-x"))
    provider = get_asr_provider("openai")
    assert isinstance(provider, OpenAIAsrProvider)
    expected_s = asr_module.OPENAI_PIECE_SECONDS * 0.5
    assert provider.client.timeout >= 2.5 * expected_s


class _FakeTranscriptions:
    """Simula la API: cada trozo responde con una frase de su hablante "A"."""

    def __init__(
        self,
        barrier: threading.Barrier | None = None,
        fail_on: str = "",
        reject_references: bool = False,
    ) -> None:
        self.barrier = barrier
        self.fail_on = fail_on
        self.reject_references = reject_references
        self.known: dict[str, list[str]] = {}

    def create(self, *, model: str, file: Any, **options: Any) -> SimpleNamespace:
        name = Path(file.name).stem
        known = options.get("known_speaker_names")
        if known:
            self.known[name] = list(known)
            assert all(
                ref.startswith("data:audio/wav;base64,")
                for ref in options["known_speaker_references"]
            )
        if self.barrier is not None and name != "piece-000":
            # Solo pasa si los trozos (salvo el primero, que va solo) están en vuelo a la vez.
            self.barrier.wait()
        if name == self.fail_on or (known and self.reject_references):
            raise openai.BadRequestError(
                "audio inválido",
                response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                body=None,
            )
        # El último trozo responde antes: el resultado debe seguir el orden del audio.
        time.sleep(0.05 if name != "piece-002" else 0)
        return SimpleNamespace(
            language="spanish",
            usage=SimpleNamespace(type="tokens", input_tokens=10, output_tokens=2),
            # Con voces de referencia, la API devuelve el nombre del hablante reconocido.
            segments=[
                SimpleNamespace(
                    text=f"hola {name}", start=1.0, end=5.0, speaker=known[0] if known else "A"
                )
            ],
        )


def _pieces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    pieces = []
    for i in range(3):
        piece = tmp_path / f"piece-{i:03d}.ogg"
        piece.write_bytes(b"x")
        pieces.append((piece, i * 1_200_000))
    monkeypatch.setattr(asr_module, "_fits_in_one_request", lambda _: False)
    monkeypatch.setattr(asr_module, "split_audio", lambda *_: pieces)
    return audio


def test_openai_pieces_are_sent_in_parallel_and_merged_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _pieces(tmp_path, monkeypatch)
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=_FakeTranscriptions(threading.Barrier(2, timeout=5)))
    )
    result = OpenAIAsrProvider(client, "gpt-4o-transcribe-diarize").transcribe(audio, None)

    assert [s.text for s in result.segments] == [
        "hola piece-000",
        "hola piece-001",
        "hola piece-002",
    ]
    assert [s.start_ms for s in result.segments] == [1_000, 1_201_000, 2_401_000]
    # Sin muestras de voz (aquí el recorte falla: los trozos no son audio), cada trozo
    # conserva sus propias etiquetas.
    assert [s.speaker for s in result.segments] == ["1-A", "2-A", "3-A"]
    assert (result.input_tokens, result.output_tokens) == (30, 6)
    assert result.language == "es"


def _fake_clips(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    cuts: list[tuple[int, int]] = []

    def cut_clip(source: Path, target: Path, start_ms: int, duration_ms: int) -> None:
        cuts.append((start_ms, duration_ms))
        target.write_bytes(b"RIFF")

    monkeypatch.setattr(asr_module, "cut_clip", cut_clip)
    return cuts


def test_openai_later_pieces_reuse_the_speakers_of_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _pieces(tmp_path, monkeypatch)
    cuts = _fake_clips(monkeypatch)
    fake = _FakeTranscriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=fake))
    result = OpenAIAsrProvider(client, "gpt-4o-transcribe-diarize").transcribe(audio, None)

    # La voz del hablante del primer trozo viaja con los demás, que la reconocen.
    assert fake.known == {"piece-001": ["speaker1"], "piece-002": ["speaker1"]}
    assert cuts == [(1_150, 3_700)]
    assert [s.speaker for s in result.segments] == ["1-A", "1-A", "1-A"]


def test_openai_rejected_speaker_references_fall_back_to_plain_pieces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _pieces(tmp_path, monkeypatch)
    _fake_clips(monkeypatch)
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=_FakeTranscriptions(reject_references=True))
    )
    result = OpenAIAsrProvider(client, "gpt-4o-transcribe-diarize").transcribe(audio, None)

    assert [s.speaker for s in result.segments] == ["1-A", "2-A", "3-A"]


def test_openai_without_diarization_sends_no_speaker_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _pieces(tmp_path, monkeypatch)
    _fake_clips(monkeypatch)
    fake = _FakeTranscriptions(threading.Barrier(2, timeout=5))
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=fake))
    OpenAIAsrProvider(client, "whisper-1").transcribe(audio, None)

    assert fake.known == {}


def test_openai_failed_piece_fails_the_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _pieces(tmp_path, monkeypatch)
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=_FakeTranscriptions(fail_on="piece-001"))
    )
    with pytest.raises(AsrError) as exc:
        OpenAIAsrProvider(client, "gpt-4o-transcribe-diarize").transcribe(audio, None)
    assert exc.value.transient is False


# --- ElevenLabs ----------------------------------------------------------------------------


def _word(text: str, start: float, end: float, speaker: str | None = "speaker_0") -> dict[str, Any]:
    return {
        "text": text, "start": start, "end": end, "type": "word", "speaker_id": speaker,
        "logprob": -0.1,
    }  # fmt: skip


_SPACE = {"text": " ", "start": 0.0, "end": 0.0, "type": "spacing", "speaker_id": None}


def test_elevenlabs_words_become_sentences_per_speaker() -> None:
    words = [
        _word("Hola,", 0.0, 0.4), _SPACE, _word("Laura.", 0.5, 0.9), _SPACE,
        _word("¿Empezamos?", 1.0, 1.6), _SPACE,
        # Cambio de hablante a media frase y evento de audio, que se ignora.
        _word("Sí", 1.7, 1.9, "speaker_1"), _SPACE,
        {"text": "(risas)", "start": 1.9, "end": 2.4, "type": "audio_event", "speaker_id": None},
        _SPACE, _word("claro", 2.5, 2.9, "speaker_1"), _SPACE,
        # Pausa larga sin puntuación: frase nueva.
        _word("vamos", 6.0, 6.4, "speaker_1"),
    ]  # fmt: skip
    segments = asr_module._segments_from_words(words)

    assert [(s.speaker, s.text, s.start_ms, s.end_ms) for s in segments] == [
        ("speaker_0", "Hola, Laura.", 0, 900),
        ("speaker_0", "¿Empezamos?", 1_000, 1_600),
        ("speaker_1", "Sí claro", 1_700, 2_900),
        ("speaker_1", "vamos", 6_000, 6_400),
    ]
    assert segments[0].confidence == 0.905


def _elevenlabs(handler: Any, *, diarize: bool = True) -> asr_module.ElevenLabsAsrProvider:
    http = httpx.Client(base_url="http://elevenlabs", transport=httpx.MockTransport(handler))
    return asr_module.ElevenLabsAsrProvider(http, "scribe_v3", diarize=diarize)


def test_elevenlabs_sends_the_whole_recording_in_one_request(tmp_path: Path) -> None:
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "language_code": "spa",
                "text": "Hola. Adiós.",
                "words": [_word("Hola.", 0.0, 0.5), _SPACE, _word("Adiós.", 0.6, 1.0, "speaker_1")],
            },
        )

    result = _elevenlabs(handler).transcribe(audio, None)

    assert len(requests) == 1
    assert requests[0].url.path == "/v1/speech-to-text"
    body = requests[0].content.decode(errors="replace")
    assert 'name="model_id"\r\n\r\nscribe_v3' in body
    assert 'name="diarize"\r\n\r\ntrue' in body
    assert "language_code" not in body  # sin idioma pedido, lo detecta el modelo
    assert (result.provider, result.model, result.language) == ("elevenlabs", "scribe_v3", "es")
    assert [(s.speaker, s.text) for s in result.segments] == [
        ("speaker_0", "Hola."),
        ("speaker_1", "Adiós."),
    ]


@pytest.mark.parametrize(("status", "transient"), [(429, True), (503, True), (401, False)])
def test_elevenlabs_errors(tmp_path: Path, status: int, transient: bool) -> None:
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    provider = _elevenlabs(lambda _: httpx.Response(status, json={"detail": "x"}))
    with pytest.raises(AsrError) as exc:
        provider.transcribe(audio, "es")
    assert exc.value.transient is transient


def test_elevenlabs_engine_needs_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    current = get_settings()
    monkeypatch.setattr(current, "elevenlabs_api_key", SecretStr(""))
    assert "elevenlabs" not in current.available_engines

    monkeypatch.setattr(current, "elevenlabs_api_key", SecretStr("sk_x"))
    assert "elevenlabs" in current.available_engines
    assert current.default_engine != "elevenlabs"  # disponible, pero no el predeterminado
    provider = get_asr_provider("elevenlabs")
    assert isinstance(provider, asr_module.ElevenLabsAsrProvider)
    assert get_diarizer("elevenlabs") is None  # los hablantes vienen en la transcripción

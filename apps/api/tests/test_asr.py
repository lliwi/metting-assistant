import pytest

from app.ai.asr import is_hallucination


@pytest.mark.parametrize(
    "text",
    [
        "¡Suscríbete!",
        "¡Suscríbete y dale like!",
        "Subtítulos realizados por la comunidad de Amara.org",
        "Gracias por ver el vídeo.",
        "Thanks for watching!",
    ],
)
def test_known_hallucinations_are_dropped(text: str) -> None:
    assert is_hallucination(text, no_speech_prob=0.1, avg_logprob=-0.2)


@pytest.mark.parametrize(
    "text",
    [
        "Laura, suscríbete al boletín de proveedores y revisa el contrato.",
        "Gracias por venir a la reunión.",
        "Buenos días a todos.",
    ],
)
def test_real_speech_is_kept(text: str) -> None:
    assert not is_hallucination(text, no_speech_prob=0.1, avg_logprob=-0.3)


def test_unlikely_speech_with_low_certainty_is_dropped() -> None:
    # Caso real: captura con huecos, Whisper casi seguro de que no había voz.
    assert is_hallucination("Hola qué tal", no_speech_prob=0.74, avg_logprob=-0.97)
    assert not is_hallucination("Hola qué tal", no_speech_prob=0.74, avg_logprob=-0.3)
    assert not is_hallucination("Hola qué tal", no_speech_prob=0.2, avg_logprob=-1.2)


def test_gpu_out_of_memory_is_transient(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Con la GPU compartida (Ollama + Whisper), quedarse sin VRAM se reintenta más tarde."""
    from app.ai import asr

    class Exploding:
        def transcribe(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("CUDA failed with error out of memory")

    monkeypatch.setattr(asr, "_load_whisper", lambda *a: Exploding())
    monkeypatch.setattr(asr._load_whisper, "cache_clear", lambda: None, raising=False)
    provider = asr.FasterWhisperProvider("turbo", "cuda", "int8_float16", str(tmp_path), 5)
    with pytest.raises(asr.AsrError) as exc:
        provider.transcribe(tmp_path / "a.ogg", "es")
    assert exc.value.transient


@pytest.mark.parametrize(
    ("captured", "timeline", "gaps"),
    [
        (4_080, 11_211, True),  # captura con pérdida real
        (6_840, 7_197, False),  # grabación corta completa (antes, falso positivo)
        (6_840, 8_462, False),  # < 2 s de diferencia: arranque de la grabadora
        (100_000, 120_000, True),
    ],
)
def test_capture_gap_detection(captured: int, timeline: int, gaps: bool) -> None:
    from app.media.pipeline import has_capture_gaps

    assert has_capture_gaps(captured, timeline) is gaps

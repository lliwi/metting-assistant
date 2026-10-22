"""Comprueba la configuración de IA: `python -m app.ai.check` (en el contenedor del worker)."""

import sys
import time

from app.ai.asr import get_asr_provider
from app.ai.llm import LlmError, get_llm_provider
from app.core.config import get_settings

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "idioma": {"type": "string"}},
    "required": ["ok", "idioma"],
    "additionalProperties": False,
}


def main() -> int:
    settings = get_settings()
    asr = get_asr_provider()
    model = getattr(asr, "model", "")
    if asr.name == "faster_whisper":  # type: ignore[attr-defined]
        device = f"{settings.whisper_device}/{settings.whisper_compute_type}"
        print(f"ASR: faster_whisper (modelo {model}, {device})")
    else:
        print(f"ASR: openai (modelo {model}, {settings.openai_base_url})")
    print(f"Hablantes: {settings.diarization_provider}")

    llm = get_llm_provider()
    print(f"LLM: {llm.name} (modelo {llm.model})", flush=True)
    started = time.monotonic()
    try:
        answer = llm.complete_json(
            "Responde solo con el JSON pedido.",
            "Devuelve ok=true e idioma='es'.",
            SCHEMA,
        )
    except LlmError as exc:
        print(f"  ERROR: {exc}")
        return 1
    print(f"  respuesta {answer} en {time.monotonic() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

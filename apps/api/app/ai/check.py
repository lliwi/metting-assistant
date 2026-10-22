"""Comprueba la configuración de IA: `python -m app.ai.check` (en el contenedor del worker)."""

import sys
import time

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
    print(f"ASR: {settings.asr_provider}", end=" ")
    if settings.asr_provider == "faster_whisper":
        device = f"{settings.whisper_device}/{settings.whisper_compute_type}"
        print(f"(modelo {settings.whisper_model}, {device})")
    else:
        print(f"(modelo {settings.openai_transcription_model}, {settings.openai_base_url})")

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

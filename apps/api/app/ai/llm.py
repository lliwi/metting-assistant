"""Modelos de lenguaje con salida JSON validada por esquema (resúmenes, actas, tareas)."""

import json
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


class LlmError(Exception):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class LlmProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def _parse(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Respuesta no JSON: {content[:200]}", transient=False) from exc
    if not isinstance(data, dict):
        raise LlmError("La respuesta no es un objeto JSON", transient=False)
    return data


class OllamaProvider:
    """API nativa de Ollama: permite fijar num_ctx y restringir la salida a un esquema."""

    name = "ollama"

    def __init__(
        self, http: httpx.Client, model: str, num_ctx: int, keep_alive: str = "5m"
    ) -> None:
        self.http = http
        self.model = model
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.http.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "keep_alive": self.keep_alive,
                    "format": schema,
                    "options": {"temperature": 0, "num_ctx": self.num_ctx},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
        except httpx.TransportError as exc:
            raise LlmError(f"Ollama no responde: {exc}", transient=True) from exc
        if response.status_code >= 500:
            raise LlmError(f"Ollama {response.status_code}: {response.text[:200]}", transient=True)
        if response.status_code >= 400:
            raise LlmError(f"Ollama {response.status_code}: {response.text[:200]}", transient=False)
        return _parse(response.json()["message"]["content"])


class OpenAILlmProvider:
    name = "openai"

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        # Los modelos de razonamiento (GPT-5…) solo admiten la temperatura por defecto: se
        # detecta en la primera llamada y se deja de enviar.
        self.fixed_temperature = False

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        import openai

        transient = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)
        try:
            try:
                completion = self._create(system, user, schema)
            except openai.BadRequestError as exc:
                if getattr(exc, "param", None) != "temperature" or self.fixed_temperature:
                    raise
                self.fixed_temperature = True
                completion = self._create(system, user, schema)
        except transient as exc:
            raise LlmError(str(exc), transient=True) from exc
        except openai.APIError as exc:
            raise LlmError(str(exc), transient=False) from exc
        return _parse(completion.choices[0].message.content or "")

    def _create(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        return self.client.chat.completions.create(
            model=self.model,
            **({} if self.fixed_temperature else {"temperature": 0}),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
        )


def get_llm_provider() -> LlmProvider:
    settings = get_settings()
    if settings.llm_provider == "openai":
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=600,
            max_retries=2,
        )
        return OpenAILlmProvider(client, settings.openai_llm_model)
    http = httpx.Client(
        base_url=settings.ollama_base_url.rstrip("/"), timeout=settings.ollama_timeout_seconds
    )
    return OllamaProvider(
        http, settings.ollama_model, settings.ollama_num_ctx, settings.ollama_keep_alive
    )

"""Consumo de los trabajos de IA y su coste estimado según AI_PRICES."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import ModelPrice, get_settings
from app.models import UsageRecord

# Proveedores que se ejecutan en nuestra infraestructura: sin coste por uso.
LOCAL_PROVIDERS = frozenset({"faster_whisper", "ollama"})


@dataclass
class Usage:
    """Tokens acumulados por un proveedor de LLM (un documento puede hacer varias llamadas)."""

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int | None, output_tokens: int | None) -> None:
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0

    def since(self, start: "Usage") -> "Usage":
        return Usage(
            self.input_tokens - start.input_tokens, self.output_tokens - start.output_tokens
        )

    def copy(self) -> "Usage":
        return Usage(self.input_tokens, self.output_tokens)


def estimate_cost(
    provider: str, model: str, *, input_tokens: int, output_tokens: int, audio_ms: int
) -> Decimal | None:
    """USD. 0 para proveedores locales; None si el modelo no tiene precio configurado."""
    if provider in LOCAL_PROVIDERS:
        return Decimal(0)
    price: ModelPrice | None = get_settings().ai_prices.get(model)
    if price is None:
        return None
    cost = (
        Decimal(str(price.input_per_mtok)) * input_tokens / 1_000_000
        + Decimal(str(price.output_per_mtok)) * output_tokens / 1_000_000
        + Decimal(str(price.per_audio_minute)) * audio_ms / 60_000
    )
    return cost.quantize(Decimal("0.000001"))


def record_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    recording_id: uuid.UUID,
    kind: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    audio_ms: int = 0,
    processing_ms: int = 0,
) -> UsageRecord:
    """Se añade a la transacción en curso (se confirma con el trabajo)."""
    row = UsageRecord(
        organization_id=organization_id,
        recording_id=recording_id,
        kind=kind,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        audio_ms=audio_ms,
        processing_ms=processing_ms,
        cost_usd=estimate_cost(
            provider, model, input_tokens=input_tokens, output_tokens=output_tokens,
            audio_ms=audio_ms,
        ),
    )  # fmt: skip
    db.add(row)
    return row

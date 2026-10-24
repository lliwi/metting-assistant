"""Consumo de IA por trabajo y su coste estimado (AI_PRICES)."""

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.usage import estimate_cost
from app.core.config import ModelPrice, get_settings
from app.models import Membership, Role, UsageRecord, User
from tests.conftest import ApiClient
from tests.test_ai import ana, queue, transcribed  # noqa: F401 - fixtures
from tests.test_exports import documented  # noqa: F401 - fixture


@pytest.fixture
def prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        get_settings(),
        "ai_prices",
        {
            "fake-diarize": ModelPrice(per_audio_minute=0.006),
            "fake-llm": ModelPrice(input_per_mtok=1.25, output_per_mtok=10),
        },
    )


def test_cost_estimation(prices: None) -> None:
    # 1 000 000 tokens de entrada (1,25) + 100 000 de salida (1,00).
    assert estimate_cost(
        "openai", "fake-llm", input_tokens=1_000_000, output_tokens=100_000, audio_ms=0
    ) == Decimal("2.250000")
    # 10 minutos de audio.
    assert estimate_cost(
        "openai", "fake-diarize", input_tokens=0, output_tokens=0, audio_ms=600_000
    ) == Decimal("0.060000")
    # Local: gratis aunque no tenga precio; desconocido: sin coste.
    assert estimate_cost("ollama", "gemma3:4b", input_tokens=9, output_tokens=9, audio_ms=0) == 0
    assert estimate_cost("openai", "otro", input_tokens=9, output_tokens=9, audio_ms=0) is None


@pytest.mark.db
def test_jobs_record_usage_and_cost(
    prices: None,
    documented: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
    db: Session,
) -> None:
    rows = {r.kind: r for r in db.scalars(select(UsageRecord))}
    assert set(rows) == {"transcription", "summary", "minutes"}
    transcription = rows["transcription"]
    assert (transcription.provider, transcription.model) == ("fake", "fake-diarize")
    assert transcription.audio_ms > 0
    assert transcription.cost_usd is not None
    # Una llamada por documento: 1 000 tokens de entrada y 200 de salida (FakeLlm).
    summary = rows["summary"]
    assert (summary.input_tokens, summary.output_tokens) == (1_000, 200)
    assert summary.cost_usd == Decimal("0.003250")

    org_id = documented["organization_id"]
    usage = ana.get(f"/v1/organizations/{org_id}/usage").json()
    assert {i["kind"] for i in usage["items"]} == {"transcription", "summary", "minutes"}
    assert usage["unpriced"] is False
    assert Decimal(usage["total_cost_usd"]) == sum(
        (r.cost_usd or Decimal(0) for r in rows.values()), Decimal(0)
    )
    assert (
        ana.get(f"/v1/organizations/{org_id}/usage", params={"month": "2020-01"}).json()["items"]
        == []
    )
    bad = ana.get(f"/v1/organizations/{org_id}/usage", params={"month": "2020-13"})
    assert bad.status_code == 422


@pytest.mark.db
def test_unpriced_usage_is_flagged(
    documented: dict[str, Any],  # noqa: F811
    ana: ApiClient,  # noqa: F811
) -> None:
    usage = ana.get(f"/v1/organizations/{documented['organization_id']}/usage").json()
    assert usage["unpriced"] is True
    assert all(i["cost_usd"] is None for i in usage["items"])
    assert Decimal(usage["total_cost_usd"]) == 0


@pytest.mark.db
def test_usage_is_for_admins_only(
    transcribed: dict[str, Any],  # noqa: F811
    db: Session,
    make_user: Callable[..., User],
    login: Callable[[str], ApiClient],
) -> None:
    org_id = transcribed["organization_id"]
    member = make_user("max@example.com")
    db.add(Membership(user_id=member.id, organization_id=uuid.UUID(org_id), role=Role.MEMBER))
    db.commit()
    assert login("max@example.com").get(f"/v1/organizations/{org_id}/usage").status_code == 403
    make_user("eve@example.com")
    assert login("eve@example.com").get(f"/v1/organizations/{org_id}/usage").status_code == 404

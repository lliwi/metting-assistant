"""Idiomas del audio admitidos para transcribir (es, ca, en, it)."""

import pytest

from app.ai.asr import _iso_language
from app.ai.documents import LANGUAGE_NAMES
from tests.conftest import ApiClient
from tests.test_pipeline import ana  # noqa: F401 - fixture


@pytest.mark.db
@pytest.mark.parametrize(("language", "status"), [("it", 201), ("en", 201), ("fr", 422)])
def test_recording_language(ana: ApiClient, language: str, status: int) -> None:  # noqa: F811
    org_id = ana.get("/v1/organizations").json()[0]["id"]
    res = ana.post(
        "/v1/recordings",
        json={
            "organization_id": org_id,
            "title": "Riunione",
            "language": language,
            "consent": True,
        },
    )
    assert res.status_code == status, res.text
    if status == 201:
        assert res.json()["language"] == language


def test_italian_is_normalized_and_named_for_the_documents() -> None:
    assert _iso_language("Italian") == "it"
    assert LANGUAGE_NAMES["it"] == "italiano"

"""Tests contra PostgreSQL real. Requieren TEST_DATABASE_URL (base de datos desechable)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL no definida")

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def migrated_url(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[str]:
    assert TEST_DATABASE_URL
    monkeypatch_module.setenv("DATABASE_URL", TEST_DATABASE_URL)
    from app.core.config import get_settings

    get_settings.cache_clear()
    cfg = Config(str(API_ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield TEST_DATABASE_URL
    command.downgrade(cfg, "base")
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


def test_downgrade_and_upgrade_are_reversible(migrated_url: str) -> None:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_email_is_unique_case_insensitive(migrated_url: str) -> None:
    engine = create_engine(migrated_url)
    with Session(engine) as session:
        session.add_all(
            [User(email="ana@example.com", name="Ana"), User(email="bob@example.com", name="Bob")]
        )
        session.commit()

        session.add(User(email="ANA@example.com", name="Ana bis"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.execute(text("DELETE FROM users"))
        session.commit()
    engine.dispose()

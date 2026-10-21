import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from tests.conftest import alembic_config

pytestmark = pytest.mark.db


def test_downgrade_and_upgrade_are_reversible(migrated_db: None) -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_no_pending_model_changes(migrated_db: None) -> None:
    command.check(alembic_config())


def test_email_is_unique_case_insensitive(db: Session) -> None:
    db.add_all(
        [User(email="ana@example.com", name="Ana"), User(email="bob@example.com", name="Bob")]
    )
    db.commit()

    db.add(User(email="ANA@example.com", name="Ana bis"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_enum_check_constraints_exist_once(db: Session) -> None:
    names = db.scalars(
        text(
            "SELECT conname FROM pg_constraint WHERE contype = 'c' "
            "AND conrelid = 'organization_invitations'::regclass"
        )
    ).all()
    assert names == ["ck_organization_invitations_invitation_role"]

"""class notes ai output kind

Revision ID: 5c1e7a9d2f43
Revises: 2141ae219858
Create Date: 2026-10-24 19:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1e7a9d2f43"
down_revision: str | Sequence[str] | None = "2141ae219858"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# La convención de nombres añade el prefijo "ck_ai_outputs_" (ck_ai_outputs_ai_output_kind).
CONSTRAINT = "ai_output_kind"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(f"ck_ai_outputs_{CONSTRAINT}"), "ai_outputs", type_="check")
    op.create_check_constraint(
        CONSTRAINT, "ai_outputs", "kind IN ('summary', 'minutes', 'class_notes')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM ai_outputs WHERE kind = 'class_notes'")
    op.drop_constraint(op.f(f"ck_ai_outputs_{CONSTRAINT}"), "ai_outputs", type_="check")
    op.create_check_constraint(CONSTRAINT, "ai_outputs", "kind IN ('summary', 'minutes')")

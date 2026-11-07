"""recording audio gaps

Revision ID: 7d3e9a41c2b8
Revises: 9965a49d0dd5
Create Date: 2026-11-07 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7d3e9a41c2b8"
down_revision: str | Sequence[str] | None = "9965a49d0dd5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recordings",
        sa.Column(
            "audio_gaps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recordings", "audio_gaps")

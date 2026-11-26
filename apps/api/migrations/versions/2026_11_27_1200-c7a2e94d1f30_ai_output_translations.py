"""ai output translations

Revision ID: c7a2e94d1f30
Revises: b41f0c6d8e27
Create Date: 2026-11-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c7a2e94d1f30"
down_revision: str | Sequence[str] | None = "b41f0c6d8e27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_output_translations",
        sa.Column("output_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "ready",
                "failed",
                name="document_translation_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')", name="document_translation_status"
        ),
        sa.ForeignKeyConstraint(
            ["output_id"],
            ["ai_outputs.id"],
            name=op.f("fk_ai_output_translations_output_id_ai_outputs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_ai_output_translations_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_output_translations")),
    )
    op.create_index(
        "uq_ai_output_translations_language",
        "ai_output_translations",
        ["output_id", "language"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_ai_output_translations_language", table_name="ai_output_translations")
    op.drop_table("ai_output_translations")

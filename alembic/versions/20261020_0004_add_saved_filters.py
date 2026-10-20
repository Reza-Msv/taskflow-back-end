"""add saved filters

Revision ID: 20261020_0004
Revises: 20261020_0003
Create Date: 2026-10-20 15:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261020_0004"
down_revision: str | None = "20261020_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_filters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("filter_criteria", sa.JSON(), nullable=False),
        sa.Column("sort_by", sa.String(length=50), nullable=True),
        sa.Column("sort_dir", sa.String(length=10), nullable=True),
        sa.Column(
            "view_type",
            sa.String(length=50),
            server_default="list",
            nullable=False,
        ),
        sa.Column(
            "is_favorite",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_saved_filters_user_id",
        "saved_filters",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_saved_filters_org_user",
        "saved_filters",
        ["organization_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_saved_filters_org_user", table_name="saved_filters")
    op.drop_index("ix_saved_filters_user_id", table_name="saved_filters")
    op.drop_table("saved_filters")

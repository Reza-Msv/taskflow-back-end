"""add work item completed_at column

Revision ID: 20261027_0006
Revises: 20261027_0005
Create Date: 2026-10-27 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_0006"
down_revision: str | None = "20261027_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add completed_at column to work_items
    op.add_column(
        "work_items",
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_work_items_completed_at",
        "work_items",
        ["completed_at"],
    )
    op.create_index(
        "ix_work_items_project_completed_at",
        "work_items",
        ["project_id", "completed_at"],
    )

    # 2. Backfill completed_at for existing work_items in completed status
    # using updated_at as reliable completion timestamp fallback
    op.execute(
        """
        UPDATE work_items
        SET completed_at = updated_at
        WHERE lower(status) IN ('done', 'completed', 'closed', 'resolved')
          AND completed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_work_items_project_completed_at", table_name="work_items")
    op.drop_index("ix_work_items_completed_at", table_name="work_items")
    op.drop_column("work_items", "completed_at")

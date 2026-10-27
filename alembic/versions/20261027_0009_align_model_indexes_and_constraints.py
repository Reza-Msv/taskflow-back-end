"""align model indexes and constraints

Revision ID: 20261027_0009
Revises: 20261027_0008
Create Date: 2026-10-27 21:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_0009"
down_revision: str | None = "20261027_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add organization_id index on notifications
    op.create_index(
        "ix_notifications_organization_id",
        "notifications",
        ["organization_id"],
        unique=False,
    )
    # Add organization_id and project_id indexes on saved_filters
    op.create_index(
        "ix_saved_filters_organization_id",
        "saved_filters",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_saved_filters_project_id",
        "saved_filters",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_saved_filters_project_id", table_name="saved_filters")
    op.drop_index("ix_saved_filters_organization_id", table_name="saved_filters")
    op.drop_index("ix_notifications_organization_id", table_name="notifications")

"""add performance indexes for query optimization

Revision ID: 20261027_0009
Revises: 20261027_0008
Create Date: 2026-10-27 20:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_0009"
down_revision: str | None = "20261027_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Indexes on work_items
    op.create_index(
        "ix_work_items_due_date",
        "work_items",
        ["due_date"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_project_status",
        "work_items",
        ["project_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_project_assignee",
        "work_items",
        ["project_id", "assignee_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_project_due_date",
        "work_items",
        ["project_id", "due_date"],
        unique=False,
    )

    # 2. Indexes on notifications
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", "is_read"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
    )

    # 3. Index on work_item_activities
    op.create_index(
        "ix_work_item_activities_item_created",
        "work_item_activities",
        ["work_item_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_item_activities_item_created",
        table_name="work_item_activities",
    )
    op.drop_index(
        "ix_notifications_user_created",
        table_name="notifications",
    )
    op.drop_index(
        "ix_notifications_user_unread",
        table_name="notifications",
    )
    op.drop_index(
        "ix_work_items_project_due_date",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_project_assignee",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_project_status",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_due_date",
        table_name="work_items",
    )

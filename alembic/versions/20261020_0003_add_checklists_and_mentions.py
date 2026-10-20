"""add checklists and mentions

Revision ID: 20261020_0003
Revises: 20261017_0002
Create Date: 2026-10-20 12:00:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261020_0003"
down_revision: str | None = "20261017_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Checklist Items
    op.create_table(
        "checklist_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "is_completed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checklist_items_work_item_id",
        "checklist_items",
        ["work_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_checklist_items_assignee_id",
        "checklist_items",
        ["assignee_id"],
        unique=False,
    )

    # 2. Comment Mentions
    op.create_table(
        "comment_mentions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("comment_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"], ["work_item_comments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "comment_id", "user_id", name="uq_comment_mentions_comment_user"
        ),
    )
    op.create_index(
        "ix_comment_mentions_comment_id",
        "comment_mentions",
        ["comment_id"],
        unique=False,
    )
    op.create_index(
        "ix_comment_mentions_user_id", "comment_mentions", ["user_id"], unique=False
    )


def downgrade() -> None:
    # 2. Comment Mentions
    op.drop_index("ix_comment_mentions_user_id", table_name="comment_mentions")
    op.drop_index("ix_comment_mentions_comment_id", table_name="comment_mentions")
    op.drop_table("comment_mentions")

    # 1. Checklist Items
    op.drop_index("ix_checklist_items_assignee_id", table_name="checklist_items")
    op.drop_index("ix_checklist_items_work_item_id", table_name="checklist_items")
    op.drop_table("checklist_items")

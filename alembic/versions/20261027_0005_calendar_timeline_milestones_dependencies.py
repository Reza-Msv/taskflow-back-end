"""add calendar timeline milestones and dependencies

Revision ID: 20261027_0005
Revises: 20261020_0004
Create Date: 2026-10-27 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_0005"
down_revision: str | None = "20261020_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create milestones table
    op.create_table(
        "milestones",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_completed",
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
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_milestones_project_name"
        ),
    )
    op.create_index(
        "ix_milestones_project_id",
        "milestones",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_milestones_target_date",
        "milestones",
        ["target_date"],
        unique=False,
    )
    op.create_index(
        "ix_milestones_is_completed",
        "milestones",
        ["is_completed"],
        unique=False,
    )

    # 2. Add scheduling fields to work_items
    op.add_column(
        "work_items",
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "work_items",
        sa.Column("milestone_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_work_items_milestone_id",
        "work_items",
        "milestones",
        ["milestone_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_work_items_start_date",
        "work_items",
        ["start_date"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_milestone_id",
        "work_items",
        ["milestone_id"],
        unique=False,
    )

    # 3. Create task_dependencies table
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("predecessor_id", sa.UUID(), nullable=False),
        sa.Column("successor_id", sa.UUID(), nullable=False),
        sa.Column(
            "dependency_type",
            sa.String(length=50),
            server_default="finish_to_start",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_id"], ["work_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["successor_id"], ["work_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "predecessor_id",
            "successor_id",
            name="uq_task_dependencies_pred_succ",
        ),
        sa.CheckConstraint(
            "predecessor_id != successor_id",
            name="ck_task_dependencies_no_self_ref",
        ),
    )
    op.create_index(
        "ix_task_dependencies_project_id",
        "task_dependencies",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_task_dependencies_predecessor_id",
        "task_dependencies",
        ["predecessor_id"],
        unique=False,
    )
    op.create_index(
        "ix_task_dependencies_successor_id",
        "task_dependencies",
        ["successor_id"],
        unique=False,
    )


def downgrade() -> None:
    # 3. Drop task_dependencies
    op.drop_index(
        "ix_task_dependencies_successor_id", table_name="task_dependencies"
    )
    op.drop_index(
        "ix_task_dependencies_predecessor_id", table_name="task_dependencies"
    )
    op.drop_index(
        "ix_task_dependencies_project_id", table_name="task_dependencies"
    )
    op.drop_table("task_dependencies")

    # 2. Revert work_items scheduling columns
    op.drop_index("ix_work_items_milestone_id", table_name="work_items")
    op.drop_index("ix_work_items_start_date", table_name="work_items")
    op.drop_constraint(
        "fk_work_items_milestone_id", "work_items", type_="foreignkey"
    )
    op.drop_column("work_items", "milestone_id")
    op.drop_column("work_items", "start_date")

    # 1. Drop milestones
    op.drop_index("ix_milestones_is_completed", table_name="milestones")
    op.drop_index("ix_milestones_target_date", table_name="milestones")
    op.drop_index("ix_milestones_project_id", table_name="milestones")
    op.drop_table("milestones")

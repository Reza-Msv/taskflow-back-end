"""add advanced project management tables

Revision ID: 20261027_0008
Revises: 20261027_0007
Create Date: 2026-10-27 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_0008"
down_revision: str | None = "20261027_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Task Templates
    op.create_table(
        "task_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_title", sa.String(length=255), nullable=False),
        sa.Column("task_description", sa.Text(), nullable=True),
        sa.Column(
            "task_type", sa.String(length=50), server_default="task", nullable=False
        ),
        sa.Column(
            "priority", sa.String(length=50), server_default="medium", nullable=False
        ),
        sa.Column("estimated_points", sa.Integer(), nullable=True),
        sa.Column("checklist_definitions", sa.JSON(), nullable=True),
        sa.Column("default_assignee_id", sa.UUID(), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["default_assignee_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_task_templates_project_name"
        ),
    )
    op.create_index(
        "ix_task_templates_project_id", "task_templates", ["project_id"], unique=False
    )
    op.create_index(
        "ix_task_templates_organization_id",
        "task_templates",
        ["organization_id"],
        unique=False,
    )

    # 2. Recurring Task Rules
    op.create_table(
        "recurring_task_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=50), server_default="task", nullable=False),
        sa.Column(
            "priority", sa.String(length=50), server_default="medium", nullable=False
        ),
        sa.Column("estimated_points", sa.Integer(), nullable=True),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("recurrence_pattern", sa.String(length=50), nullable=False),
        sa.Column("interval_value", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "timezone", sa.String(length=50), server_default="UTC", nullable=False
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recurring_task_rules_project_id",
        "recurring_task_rules",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_task_rules_next_run_at",
        "recurring_task_rules",
        ["next_run_at"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_task_rules_is_active",
        "recurring_task_rules",
        ["is_active"],
        unique=False,
    )

    # 3. Automation Rules
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("trigger_event", sa.String(length=100), nullable=False),
        sa.Column("trigger_conditions", sa.JSON(), nullable=True),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("action_payload", sa.JSON(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("execution_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_automation_rules_project_id",
        "automation_rules",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_automation_rules_trigger_event",
        "automation_rules",
        ["trigger_event"],
        unique=False,
    )
    op.create_index(
        "ix_automation_rules_is_active", "automation_rules", ["is_active"], unique=False
    )

    # 4. Automation Logs
    op.create_table(
        "automation_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=True),
        sa.Column("trigger_event", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("action_taken", sa.String(length=255), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["automation_rules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_automation_logs_rule_id", "automation_logs", ["rule_id"], unique=False
    )
    op.create_index(
        "ix_automation_logs_project_id", "automation_logs", ["project_id"], unique=False
    )
    op.create_index(
        "ix_automation_logs_executed_at",
        "automation_logs",
        ["executed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_automation_logs_executed_at", table_name="automation_logs")
    op.drop_index("ix_automation_logs_project_id", table_name="automation_logs")
    op.drop_index("ix_automation_logs_rule_id", table_name="automation_logs")
    op.drop_table("automation_logs")

    op.drop_index("ix_automation_rules_is_active", table_name="automation_rules")
    op.drop_index("ix_automation_rules_trigger_event", table_name="automation_rules")
    op.drop_index("ix_automation_rules_project_id", table_name="automation_rules")
    op.drop_table("automation_rules")

    op.drop_index(
        "ix_recurring_task_rules_is_active", table_name="recurring_task_rules"
    )
    op.drop_index(
        "ix_recurring_task_rules_next_run_at", table_name="recurring_task_rules"
    )
    op.drop_index(
        "ix_recurring_task_rules_project_id", table_name="recurring_task_rules"
    )
    op.drop_table("recurring_task_rules")

    op.drop_index("ix_task_templates_organization_id", table_name="task_templates")
    op.drop_index("ix_task_templates_project_id", table_name="task_templates")
    op.drop_table("task_templates")

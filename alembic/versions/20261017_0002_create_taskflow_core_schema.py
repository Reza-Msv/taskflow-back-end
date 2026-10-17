"""create taskflow core schema

Revision ID: 20261017_0002
Revises: 20261017_0001
Create Date: 2026-10-17 11:20:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261017_0002"
down_revision: str | None = "20261017_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    # 2. Organization Memberships
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=50), server_default="member", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_memberships_org_user"),
    )
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"], unique=False)
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"], unique=False)

    # 3. Invitations
    op.create_table(
        "invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), server_default="member", nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("invited_by_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invitations_token", "invitations", ["token"], unique=True)
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"], unique=False)
    op.create_index("ix_invitations_email", "invitations", ["email"], unique=False)

    # 4. Teams
    op.create_table(
        "teams",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teams_organization_id", "teams", ["organization_id"], unique=False)

    # 5. Team Memberships
    op.create_table(
        "team_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=50), server_default="member", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
    )
    op.create_index("ix_team_memberships_team_id", "team_memberships", ["team_id"], unique=False)
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"], unique=False)

    # 6. Projects
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_private", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "key", name="uq_projects_organization_key"),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"], unique=False)
    op.create_index("ix_projects_key", "projects", ["key"], unique=False)

    # 7. Project Memberships
    op.create_table(
        "project_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=50), server_default="member", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
    )
    op.create_index("ix_project_memberships_project_id", "project_memberships", ["project_id"], unique=False)
    op.create_index("ix_project_memberships_user_id", "project_memberships", ["user_id"], unique=False)

    # 8. Project Sequences (for transaction-safe atomic issue sequence allocation)
    op.create_table(
        "project_sequences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("current_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_project_sequences_project_id"),
    )
    op.create_index("ix_project_sequences_project_id", "project_sequences", ["project_id"], unique=True)

    # 9. Sprints
    op.create_table(
        "sprints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="draft", nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprints_project_id", "sprints", ["project_id"], unique=False)
    op.create_index("ix_sprints_project_status", "sprints", ["project_id", "status"], unique=False)

    # 10. Sprint Snapshots
    op.create_table(
        "sprint_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sprint_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_type", sa.String(length=50), nullable=False),
        sa.Column("total_points", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("completed_points", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("total_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scope_added_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scope_removed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprint_snapshots_sprint_id", "sprint_snapshots", ["sprint_id"], unique=False)

    # 11. Sprint Retrospectives
    op.create_table(
        "sprint_retrospectives",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sprint_id", sa.UUID(), nullable=False),
        sa.Column("what_went_well", sa.Text(), nullable=True),
        sa.Column("what_could_be_improved", sa.Text(), nullable=True),
        sa.Column("action_items", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sprint_id", name="uq_sprint_retrospectives_sprint_id"),
    )
    op.create_index("ix_sprint_retrospectives_sprint_id", "sprint_retrospectives", ["sprint_id"], unique=True)

    # 12. Workflow Statuses
    op.create_table(
        "workflow_statuses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), server_default="todo", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_statuses_project_id", "workflow_statuses", ["project_id"], unique=False)

    # 13. Workflow Transitions
    op.create_table(
        "workflow_transitions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("from_status_id", sa.UUID(), nullable=False),
        sa.Column("to_status_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["from_status_id"], ["workflow_statuses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_status_id"], ["workflow_statuses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_transitions_from_status_id", "workflow_transitions", ["from_status_id"], unique=False)
    op.create_index("ix_workflow_transitions_to_status_id", "workflow_transitions", ["to_status_id"], unique=False)

    # 14. Labels
    op.create_table(
        "labels",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("color", sa.String(length=20), server_default="#6B7280", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_labels_organization_name"),
    )
    op.create_index("ix_labels_organization_id", "labels", ["organization_id"], unique=False)

    # 15. Work Items
    op.create_table(
        "work_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("issue_key", sa.String(length=50), nullable=False),
        sa.Column("type", sa.String(length=50), server_default="task", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="todo", nullable=False),
        sa.Column("priority", sa.String(length=50), server_default="medium", nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("reporter_id", sa.UUID(), nullable=True),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("sprint_id", sa.UUID(), nullable=True),
        sa.Column("rank", sa.String(length=255), server_default="0|hzzzzz:", nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_points", sa.Float(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["work_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprints.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "sequence_number", name="uq_work_items_project_sequence"),
        sa.UniqueConstraint("project_id", "issue_key", name="uq_work_items_project_issue_key"),
    )
    op.create_index("ix_work_items_project_id", "work_items", ["project_id"], unique=False)
    op.create_index("ix_work_items_organization_id", "work_items", ["organization_id"], unique=False)
    op.create_index("ix_work_items_issue_key", "work_items", ["issue_key"], unique=False)
    op.create_index("ix_work_items_status", "work_items", ["status"], unique=False)
    op.create_index("ix_work_items_parent_id", "work_items", ["parent_id"], unique=False)
    op.create_index("ix_work_items_reporter_id", "work_items", ["reporter_id"], unique=False)
    op.create_index("ix_work_items_assignee_id", "work_items", ["assignee_id"], unique=False)
    op.create_index("ix_work_items_sprint_id", "work_items", ["sprint_id"], unique=False)
    op.create_index("ix_work_items_rank", "work_items", ["rank"], unique=False)
    op.create_index("ix_work_items_org_status", "work_items", ["organization_id", "status"], unique=False)

    # 16. Work Item Labels (M:N)
    op.create_table(
        "work_item_labels",
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("label_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_item_id", "label_id"),
    )

    # 17. Work Item Activities
    op.create_table(
        "work_item_activities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("field", sa.String(length=100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_item_activities_work_item_id", "work_item_activities", ["work_item_id"], unique=False)
    op.create_index("ix_work_item_activities_user_id", "work_item_activities", ["user_id"], unique=False)

    # 18. Attachments
    op.create_table(
        "attachments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("uploader_id", sa.UUID(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachments_work_item_id", "attachments", ["work_item_id"], unique=False)
    op.create_index("ix_attachments_uploader_id", "attachments", ["uploader_id"], unique=False)

    # 19. Work Item Comments
    op.create_table(
        "work_item_comments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_edited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_item_comments_work_item_id", "work_item_comments", ["work_item_id"], unique=False)
    op.create_index("ix_work_item_comments_author_id", "work_item_comments", ["author_id"], unique=False)

    # 20. Notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"], unique=False)
    op.create_index("ix_notifications_actor_id", "notifications", ["actor_id"], unique=False)
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"], unique=False)


def downgrade() -> None:
    # 20. Notifications
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_index("ix_notifications_actor_id", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    # 19. Comments
    op.drop_index("ix_work_item_comments_author_id", table_name="work_item_comments")
    op.drop_index("ix_work_item_comments_work_item_id", table_name="work_item_comments")
    op.drop_table("work_item_comments")

    # 18. Attachments
    op.drop_index("ix_attachments_uploader_id", table_name="attachments")
    op.drop_index("ix_attachments_work_item_id", table_name="attachments")
    op.drop_table("attachments")

    # 17. Activities
    op.drop_index("ix_work_item_activities_user_id", table_name="work_item_activities")
    op.drop_index("ix_work_item_activities_work_item_id", table_name="work_item_activities")
    op.drop_table("work_item_activities")

    # 16. Work Item Labels
    op.drop_table("work_item_labels")

    # 15. Work Items
    op.drop_index("ix_work_items_org_status", table_name="work_items")
    op.drop_index("ix_work_items_rank", table_name="work_items")
    op.drop_index("ix_work_items_sprint_id", table_name="work_items")
    op.drop_index("ix_work_items_assignee_id", table_name="work_items")
    op.drop_index("ix_work_items_reporter_id", table_name="work_items")
    op.drop_index("ix_work_items_parent_id", table_name="work_items")
    op.drop_index("ix_work_items_status", table_name="work_items")
    op.drop_index("ix_work_items_issue_key", table_name="work_items")
    op.drop_index("ix_work_items_organization_id", table_name="work_items")
    op.drop_index("ix_work_items_project_id", table_name="work_items")
    op.drop_table("work_items")

    # 14. Labels
    op.drop_index("ix_labels_organization_id", table_name="labels")
    op.drop_table("labels")

    # 13. Workflow Transitions
    op.drop_index("ix_workflow_transitions_to_status_id", table_name="workflow_transitions")
    op.drop_index("ix_workflow_transitions_from_status_id", table_name="workflow_transitions")
    op.drop_table("workflow_transitions")

    # 12. Workflow Statuses
    op.drop_index("ix_workflow_statuses_project_id", table_name="workflow_statuses")
    op.drop_table("workflow_statuses")

    # 11. Sprint Retrospectives
    op.drop_index("ix_sprint_retrospectives_sprint_id", table_name="sprint_retrospectives")
    op.drop_table("sprint_retrospectives")

    # 10. Sprint Snapshots
    op.drop_index("ix_sprint_snapshots_sprint_id", table_name="sprint_snapshots")
    op.drop_table("sprint_snapshots")

    # 9. Sprints
    op.drop_index("ix_sprints_project_status", table_name="sprints")
    op.drop_index("ix_sprints_project_id", table_name="sprints")
    op.drop_table("sprints")

    # 8. Project Sequences
    op.drop_index("ix_project_sequences_project_id", table_name="project_sequences")
    op.drop_table("project_sequences")

    # 7. Project Memberships
    op.drop_index("ix_project_memberships_user_id", table_name="project_memberships")
    op.drop_index("ix_project_memberships_project_id", table_name="project_memberships")
    op.drop_table("project_memberships")

    # 6. Projects
    op.drop_index("ix_projects_key", table_name="projects")
    op.drop_index("ix_projects_organization_id", table_name="projects")
    op.drop_table("projects")

    # 5. Team Memberships
    op.drop_index("ix_team_memberships_user_id", table_name="team_memberships")
    op.drop_index("ix_team_memberships_team_id", table_name="team_memberships")
    op.drop_table("team_memberships")

    # 4. Teams
    op.drop_index("ix_teams_organization_id", table_name="teams")
    op.drop_table("teams")

    # 3. Invitations
    op.drop_index("ix_invitations_email", table_name="invitations")
    op.drop_index("ix_invitations_organization_id", table_name="invitations")
    op.drop_index("ix_invitations_token", table_name="invitations")
    op.drop_table("invitations")

    # 2. Organization Memberships
    op.drop_index("ix_organization_memberships_user_id", table_name="organization_memberships")
    op.drop_index("ix_organization_memberships_organization_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")

    # 1. Organizations
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")

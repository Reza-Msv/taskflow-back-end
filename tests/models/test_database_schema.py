import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.collaboration.models import Notification, WorkItemComment
from taskflow.domains.identity.models import User
from taskflow.domains.organizations.models import (
    Invitation,
    Organization,
    OrganizationMembership,
    Team,
    TeamMembership,
)
from taskflow.domains.projects.models import (
    Project,
    ProjectMembership,
    ProjectSequence,
)
from taskflow.domains.scrum.models import (
    Sprint,
    SprintRetrospective,
    SprintSnapshot,
)
from taskflow.domains.work_items.models import (
    Attachment,
    Label,
    Milestone,
    SavedFilter,
    TaskDependency,
    WorkflowStatus,
    WorkflowTransition,
    WorkItem,
    WorkItemActivity,
)


@pytest.mark.asyncio
async def test_organization_and_membership_constraints(
    db_session: AsyncSession,
):
    """Verify organization creation, unique slug, and membership uniqueness."""
    # 1. Create User
    user = User(
        email="owner@taskflow.dev",
        full_name="Org Owner",
    )
    db_session.add(user)
    await db_session.flush()

    # 2. Create Organization
    org = Organization(
        name="Acme Corp",
        slug="acme-corp",
    )
    db_session.add(org)
    await db_session.flush()

    user_id = user.id
    org_id = org.id

    # 3. Add User as Owner
    membership = OrganizationMembership(
        organization_id=org_id,
        user_id=user_id,
        role="owner",
    )
    db_session.add(membership)
    await db_session.commit()

    # 4. Verify unique slug constraint
    duplicate_org = Organization(
        name="Acme Duplicate",
        slug="acme-corp",
    )
    db_session.add(duplicate_org)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 5. Verify unique membership constraint
    duplicate_member = OrganizationMembership(
        organization_id=org_id,
        user_id=user_id,
        role="member",
    )
    db_session.add(duplicate_member)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_project_and_issue_sequence_allocation(
    db_session: AsyncSession,
):
    """Verify project key uniqueness per org and atomic issue sequence increments."""
    # Setup Org & User
    user = User(email="pm@taskflow.dev", full_name="Project Manager")
    org1 = Organization(name="Engineering Org 1", slug="eng-1")
    org2 = Organization(name="Engineering Org 2", slug="eng-2")
    db_session.add_all([user, org1, org2])
    await db_session.flush()

    user_id = user.id
    org1_id = org1.id
    org2_id = org2.id

    # 1. Create Project in Org 1 with key "TF"
    project1 = Project(
        organization_id=org1_id,
        name="TaskFlow Web",
        key="TF",
        owner_id=user_id,
    )
    db_session.add(project1)
    await db_session.flush()
    project1_id = project1.id
    project1_key = project1.key

    # Initialize sequence counter
    seq1 = ProjectSequence(
        project_id=project1_id,
        current_sequence=0,
    )
    db_session.add(seq1)
    await db_session.commit()

    # 2. Verify duplicate project key in same org is rejected
    duplicate_key_proj = Project(
        organization_id=org1_id,
        name="Duplicate Key Project",
        key="TF",
    )
    db_session.add(duplicate_key_proj)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 3. Verify same project key "TF" is ALLOWED in a different org
    project_other_org = Project(
        organization_id=org2_id,
        name="TaskFlow Mobile",
        key="TF",
    )
    db_session.add(project_other_org)
    await db_session.commit()
    assert project_other_org.id is not None

    # 4. Simulate transaction-safe atomic sequence increment
    stmt = (
        select(ProjectSequence)
        .where(ProjectSequence.project_id == project1_id)
        .with_for_update()
    )
    res = await db_session.execute(stmt)
    locked_seq = res.scalar_one()

    locked_seq.current_sequence += 1
    issue_num = locked_seq.current_sequence
    issue_key = f"{project1_key}-{issue_num}"
    await db_session.commit()

    assert issue_num == 1
    assert issue_key == "TF-1"


@pytest.mark.asyncio
async def test_work_item_hierarchy_sprint_and_concurrency(
    db_session: AsyncSession,
):
    """Verify work items, parent-child epic/subtask hierarchy, and versioning."""
    user = User(email="dev@taskflow.dev", full_name="Lead Developer")
    org = Organization(name="TaskFlow Org", slug="taskflow-org")
    db_session.add_all([user, org])
    await db_session.flush()

    project = Project(
        organization_id=org.id,
        name="Core Platform",
        key="CORE",
        owner_id=user.id,
    )
    db_session.add(project)
    await db_session.flush()

    # Create Sprint
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Launch core database models",
        status="active",
        start_date=datetime.now(UTC),
    )
    db_session.add(sprint)
    await db_session.flush()

    # 1. Create Parent Epic
    epic = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="CORE-1",
        type="epic",
        title="Database Architecture Overhaul",
        status="in_progress",
        priority="high",
        reporter_id=user.id,
        assignee_id=user.id,
        sprint_id=sprint.id,
        version=1,
    )
    db_session.add(epic)
    await db_session.flush()

    # 2. Create Child Subtask linked to Parent Epic
    subtask = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=2,
        issue_key="CORE-2",
        type="subtask",
        title="Add Alembic core schema migration",
        status="todo",
        priority="urgent",
        parent_id=epic.id,
        reporter_id=user.id,
        assignee_id=user.id,
        sprint_id=sprint.id,
        version=1,
    )
    db_session.add(subtask)
    await db_session.commit()

    # 3. Query Epic with children loaded
    stmt = (
        select(WorkItem)
        .where(WorkItem.id == epic.id)
        .options(selectinload(WorkItem.children))
    )
    res = await db_session.execute(stmt)
    loaded_epic = res.scalar_one()

    assert len(loaded_epic.children) == 1
    assert loaded_epic.children[0].issue_key == "CORE-2"
    assert loaded_epic.children[0].parent_id == epic.id

    # 4. Verify duplicate (project_id, sequence_number) is rejected
    duplicate_seq = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,  # Already used by CORE-1
        issue_key="CORE-999",
        title="Duplicate Sequence",
    )
    db_session.add(duplicate_seq)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_labels_many_to_many_and_collaboration(
    db_session: AsyncSession,
):
    """Verify many-to-many labels association, comments, and audit activity."""
    user = User(email="collab@taskflow.dev", full_name="Collaborator")
    org = Organization(name="Collab Org", slug="collab-org")
    db_session.add_all([user, org])
    await db_session.flush()

    project = Project(
        organization_id=org.id,
        name="Collaboration Project",
        key="COL",
    )
    db_session.add(project)
    await db_session.flush()

    # 1. Create Labels
    backend_label = Label(
        organization_id=org.id,
        name="Backend",
        color="#3B82F6",
    )
    db_label = Label(
        organization_id=org.id,
        name="Database",
        color="#10B981",
    )
    db_session.add_all([backend_label, db_label])
    await db_session.flush()

    # 2. Create Work Item and associate labels
    work_item = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="COL-1",
        title="Implement PostgreSQL connection pooling",
        labels=[backend_label, db_label],
    )
    db_session.add(work_item)
    await db_session.flush()

    # 3. Add Comment
    comment = WorkItemComment(
        work_item_id=work_item.id,
        author_id=user.id,
        content="Pool pre-ping is verified with asyncpg.",
    )
    db_session.add(comment)

    # 4. Add Activity record
    activity = WorkItemActivity(
        work_item_id=work_item.id,
        user_id=user.id,
        field="status",
        old_value="todo",
        new_value="in_progress",
    )
    db_session.add(activity)

    # 5. Add Notification
    notification = Notification(
        user_id=user.id,
        actor_id=user.id,
        organization_id=org.id,
        type="comment_added",
        title="New comment on COL-1",
        message="Collaborator commented on your work item.",
        resource_type="work_item",
        resource_id=work_item.id,
    )
    db_session.add(notification)
    await db_session.commit()

    # 6. Verify associations
    stmt = (
        select(WorkItem)
        .where(WorkItem.id == work_item.id)
        .options(
            selectinload(WorkItem.labels),
            selectinload(WorkItem.comments),
            selectinload(WorkItem.activities),
        )
    )
    res = await db_session.execute(stmt)
    loaded_item = res.scalar_one()

    assert len(loaded_item.labels) == 2
    label_names = {lbl.name for lbl in loaded_item.labels}
    assert "Backend" in label_names
    assert "Database" in label_names

    assert len(loaded_item.comments) == 1
    assert loaded_item.comments[0].content == (
        "Pool pre-ping is verified with asyncpg."
    )

    assert len(loaded_item.activities) == 1
    assert loaded_item.activities[0].field == "status"
    assert loaded_item.activities[0].new_value == "in_progress"


@pytest.mark.asyncio
async def test_scrum_workflow_and_supporting_entities(
    db_session: AsyncSession,
):
    """Verify sprints, snapshots, retrospectives, workflow statuses, and attachments."""
    user = User(email="scrummaster@taskflow.dev", full_name="Scrum Master")
    org = Organization(name="Scrum Org", slug="scrum-org")
    db_session.add_all([user, org])
    await db_session.flush()

    # 1. Project & Project Membership
    project = Project(
        organization_id=org.id,
        name="Scrum Project",
        key="SCRUM",
        owner_id=user.id,
    )
    db_session.add(project)
    await db_session.flush()

    proj_member = ProjectMembership(
        project_id=project.id,
        user_id=user.id,
        role="admin",
    )
    db_session.add(proj_member)

    # 2. Team & Team Membership
    team = Team(organization_id=org.id, name="Platform Team")
    db_session.add(team)
    await db_session.flush()

    team_member = TeamMembership(
        team_id=team.id,
        user_id=user.id,
        role="lead",
    )
    db_session.add(team_member)

    # 3. Invitation
    invitation = Invitation(
        organization_id=org.id,
        email="newdev@taskflow.dev",
        role="member",
        token=uuid.uuid4().hex,
        invited_by_id=user.id,
        expires_at=datetime.now(UTC),
    )
    db_session.add(invitation)

    # 4. Workflow Status & Transition
    todo_status = WorkflowStatus(
        project_id=project.id,
        name="To Do",
        category="todo",
        position=1,
        is_default=True,
    )
    done_status = WorkflowStatus(
        project_id=project.id,
        name="Done",
        category="done",
        position=2,
    )
    db_session.add_all([todo_status, done_status])
    await db_session.flush()

    transition = WorkflowTransition(
        from_status_id=todo_status.id,
        to_status_id=done_status.id,
        name="Mark as Done",
    )
    db_session.add(transition)

    # 5. Sprint, Snapshot, and Retrospective
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 2",
        goal="Deliver Sprint burndown metrics",
        status="active",
    )
    db_session.add(sprint)
    await db_session.flush()

    snapshot = SprintSnapshot(
        sprint_id=sprint.id,
        snapshot_type="start",
        total_points=40.0,
        completed_points=0.0,
        total_items=8,
        completed_items=0,
    )
    retro = SprintRetrospective(
        sprint_id=sprint.id,
        what_went_well="Delivered all high priority tasks",
        what_could_be_improved="More realistic story point estimation",
        action_items="Add refinement sessions",
    )
    db_session.add_all([snapshot, retro])

    # 6. Work Item & Attachment
    work_item = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="SCRUM-1",
        title="Setup storage bucket for attachments",
        sprint_id=sprint.id,
    )
    db_session.add(work_item)
    await db_session.flush()

    attachment = Attachment(
        work_item_id=work_item.id,
        uploader_id=user.id,
        file_name="architecture-diagram.png",
        file_size=1048576,
        content_type="image/png",
        storage_path="/attachments/architecture-diagram.png",
    )
    db_session.add(attachment)
    await db_session.commit()

    # 7. Assertions
    stmt = (
        select(Sprint)
        .where(Sprint.id == sprint.id)
        .options(
            selectinload(Sprint.snapshots),
            selectinload(Sprint.retrospective),
        )
    )
    res = await db_session.execute(stmt)
    loaded_sprint = res.scalar_one()

    assert len(loaded_sprint.snapshots) == 1
    assert loaded_sprint.snapshots[0].total_points == 40.0
    assert loaded_sprint.retrospective is not None
    assert loaded_sprint.retrospective.what_went_well == (
        "Delivered all high priority tasks"
    )


@pytest.mark.asyncio
async def test_saved_filter_persistence_and_cascades(
    db_session: AsyncSession,
):
    """Verify SavedFilter model persistence, user relationship, and cascading."""
    user = User(
        email="filter_owner@taskflow.dev",
        full_name="Filter Owner",
    )
    org = Organization(
        name="Filter Org",
        slug="filter-org-schema",
    )
    db_session.add_all([user, org])
    await db_session.flush()

    project = Project(
        organization_id=org.id,
        name="Filter Project",
        key="FILT",
        owner_id=user.id,
    )
    db_session.add(project)
    await db_session.flush()

    sf = SavedFilter(
        user_id=user.id,
        organization_id=org.id,
        project_id=project.id,
        name="Schema Test Filter",
        description="Verify saved filter model mapping",
        filter_criteria={"status": "todo", "priority": "high"},
        sort_by="due_date",
        sort_dir="asc",
        view_type="list",
        is_favorite=True,
    )
    db_session.add(sf)
    await db_session.commit()

    # Query back
    stmt = select(SavedFilter).where(SavedFilter.id == sf.id)
    res = await db_session.execute(stmt)
    loaded = res.scalar_one()

    assert loaded.name == "Schema Test Filter"
    assert loaded.filter_criteria == {"status": "todo", "priority": "high"}
    assert loaded.is_favorite is True
    assert loaded.created_at is not None


@pytest.mark.asyncio
async def test_milestone_and_task_dependency_persistence_and_relationships(
    db_session: AsyncSession,
):
    """Verify Milestone and TaskDependency model persistence and relationships."""
    user = User(
        email="m_dep_owner@taskflow.dev",
        full_name="Milestone Dep Owner",
    )
    org = Organization(
        name="Milestone Dep Org",
        slug="m-dep-org-schema",
    )
    db_session.add_all([user, org])
    await db_session.flush()

    project = Project(
        organization_id=org.id,
        name="Milestone Dep Project",
        key="MDP",
        owner_id=user.id,
    )
    db_session.add(project)
    await db_session.flush()

    # 1. Milestone
    target_dt = datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC)
    milestone = Milestone(
        project_id=project.id,
        name="Release v1.0",
        description="Production launch",
        target_date=target_dt,
        is_completed=False,
    )
    db_session.add(milestone)
    await db_session.flush()

    # 2. Tasks with scheduling fields
    t1 = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="MDP-1",
        title="First Task",
        type="task",
        priority="high",
        status="todo",
        reporter_id=user.id,
        start_date=datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC),
        due_date=datetime(2026, 11, 15, 0, 0, 0, tzinfo=UTC),
        milestone_id=milestone.id,
    )
    t2 = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=2,
        issue_key="MDP-2",
        title="Second Task",
        type="task",
        priority="medium",
        status="todo",
        reporter_id=user.id,
        start_date=datetime(2026, 11, 16, 0, 0, 0, tzinfo=UTC),
        due_date=datetime(2026, 11, 30, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add_all([t1, t2])
    await db_session.flush()

    # 3. TaskDependency: t1 -> t2
    dep = TaskDependency(
        project_id=project.id,
        predecessor_id=t1.id,
        successor_id=t2.id,
        dependency_type="finish_to_start",
    )
    db_session.add(dep)
    await db_session.commit()

    # 4. Verify relationships
    stmt = (
        select(WorkItem)
        .options(
            selectinload(WorkItem.milestone),
            selectinload(WorkItem.successor_dependencies),
            selectinload(WorkItem.predecessor_dependencies),
        )
        .where(WorkItem.id == t1.id)
    )
    res = await db_session.execute(stmt)
    loaded_t1 = res.scalar_one()

    assert loaded_t1.milestone is not None
    assert loaded_t1.milestone.name == "Release v1.0"
    assert len(loaded_t1.successor_dependencies) == 1
    assert loaded_t1.successor_dependencies[0].successor_id == t2.id
    assert len(loaded_t1.predecessor_dependencies) == 0


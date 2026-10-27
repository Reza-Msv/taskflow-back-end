import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from taskflow.api.deps import (
    get_current_active_user,
    get_organization_service,
    get_project_service,
    get_report_service,
)
from taskflow.api.v1.endpoints.projects import (
    get_org_membership,
    verify_project_access,
)
from taskflow.domains.identity.models import User
from taskflow.domains.organizations.models import OrganizationMembership
from taskflow.domains.organizations.service import OrganizationService
from taskflow.domains.projects.models import Project, ProjectMembership
from taskflow.domains.projects.service import ProjectService
from taskflow.domains.reports.schemas import (
    CompletionTrendsReportResponse,
    ProjectOverviewReportResponse,
    SprintAnalyticsReportResponse,
    TeamWorkloadReportResponse,
)
from taskflow.domains.reports.service import ReportService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}/reports",
    tags=["Reports & Analytics"],
)


async def _authorize_project_view(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: User,
    org_service: OrganizationService,
    project_service: ProjectService,
) -> tuple[Project, OrganizationMembership, ProjectMembership | None]:
    """Verify active org membership and project view authorization."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    return project, org_membership, proj_membership


@router.get(
    "/overview",
    response_model=ProjectOverviewReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Project Overview report",
    description=(
        "Returns comprehensive project health metrics: total, completed, open, "
        "in-progress, overdue, unassigned, and due-soon tasks, completion percentage, "
        "and distributions by status, priority, and type."
    ),
)
async def get_project_overview(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
    due_window_days: Annotated[
        int,
        Query(
            ge=1,
            le=90,
            description="Forward-looking window in days to detect tasks due soon",
        ),
    ] = 7,
) -> ProjectOverviewReportResponse:
    """Retrieve high-level project analytics overview."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await report_service.get_project_overview(
        org_id=organization_id,
        project_id=project_id,
        due_window_days=due_window_days,
    )


@router.get(
    "/completion-trends",
    response_model=CompletionTrendsReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Task Completion Trends report",
    description=(
        "Returns time-series completion trends partitioned into uniform date buckets "
        "(day, week, or month). Evaluates actual completion timestamps with fallback "
        "to activity history."
    ),
)
async def get_completion_trends(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    start_date: Annotated[
        datetime,
        Query(description="Start timestamp of reporting window (ISO-8601 UTC)"),
    ],
    end_date: Annotated[
        datetime,
        Query(description="End timestamp of reporting window (ISO-8601 UTC)"),
    ],
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
    interval: Annotated[
        Literal["day", "week", "month"],
        Query(description="Time bucket interval for grouping: day, week, or month"),
    ] = "day",
) -> CompletionTrendsReportResponse:
    """Retrieve completion trends grouped by date intervals."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await report_service.get_completion_trends(
        org_id=organization_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )


@router.get(
    "/team-workload",
    response_model=TeamWorkloadReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Team Workload distribution report",
    description=(
        "Returns observable workload breakdown per project member: assigned active "
        "tasks, in-progress tasks, overdue assignments, completed tasks, story points, "
        "and unassigned work."
    ),
)
async def get_team_workload(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
    completed_since: Annotated[
        datetime | None,
        Query(
            description=(
                "Optional timestamp threshold for filtering completed tasks"
            )
        ),
    ] = None,
) -> TeamWorkloadReportResponse:
    """Retrieve project team workload metrics and unassigned tasks."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await report_service.get_team_workload(
        org_id=organization_id,
        project_id=project_id,
        completed_since=completed_since,
    )


@router.get(
    "/sprints",
    response_model=SprintAnalyticsReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Project Sprint Analytics and historical velocity",
    description="Returns velocity across completed sprints and sprint analytics.",
)
async def get_project_sprint_analytics(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
) -> SprintAnalyticsReportResponse:
    """Retrieve project-wide sprint analytics and historical velocity."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await report_service.get_sprint_analytics(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=None,
    )


@router.get(
    "/sprints/{sprint_id}",
    response_model=SprintAnalyticsReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Specific Sprint Analytics and Burndown report",
    description=(
        "Returns detailed planned vs completed points, committed vs completed tasks, "
        "burndown progression curve, and overall project velocity."
    ),
)
async def get_single_sprint_analytics(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
) -> SprintAnalyticsReportResponse:
    """Retrieve analytics, burndown, and completion metrics for a specific sprint."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await report_service.get_sprint_analytics(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
    )


@router.get(
    "/export",
    status_code=status.HTTP_200_OK,
    summary="Export Report to CSV",
    description=(
        "Exports requested report data to CSV format with UTF-8 BOM encoding and "
        "spreadsheet formula-injection protections."
    ),
)
async def export_report_csv(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    report_type: Annotated[
        Literal["overview", "completion_trends", "team_workload", "sprint_summary"],
        Query(
            description=(
                "Report type to export: overview, completion_trends, "
                "team_workload, or sprint_summary"
            )
        ),
    ],
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
    due_window_days: Annotated[
        int,
        Query(
            ge=1,
            le=90,
            description="Forward-looking due window in days (for overview report)",
        ),
    ] = 7,
    start_date: Annotated[
        datetime | None,
        Query(description="Start date for completion trends (defaults to 30 days ago)"),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="End date for completion trends (defaults to now)"),
    ] = None,
    interval: Annotated[
        Literal["day", "week", "month"],
        Query(description="Bucket interval for completion trends"),
    ] = "day",
    completed_since: Annotated[
        datetime | None,
        Query(description="Filter completed tasks threshold for team workload"),
    ] = None,
    sprint_id: Annotated[
        uuid.UUID | None,
        Query(description="Optional sprint ID for sprint analytics export"),
    ] = None,
) -> Response:
    """Export project report data as downloadable CSV."""
    project, _, _ = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )

    csv_content = await report_service.export_report_csv(
        org_id=organization_id,
        project_id=project_id,
        report_type=report_type,
        due_window_days=due_window_days,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
        completed_since=completed_since,
        sprint_id=sprint_id,
    )

    filename = f"{project.key.lower()}_{report_type}_report.csv"

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )

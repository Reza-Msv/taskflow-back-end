import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.projects.repository import ProjectRepository
from taskflow.domains.reports.repository import ReportRepository
from taskflow.domains.reports.schemas import (
    CompletionTrendsReportResponse,
    DistributionItem,
    MemberWorkloadItem,
    MemberWorkloadUser,
    ProjectOverviewReportResponse,
    SprintAnalyticsReportResponse,
    StatusDistributionItem,
    TeamWorkloadReportResponse,
    TrendBucketItem,
    UnassignedWorkItem,
)
from taskflow.domains.scrum.service import ScrumService
from taskflow.domains.work_items.repository import COMPLETED_STATUSES


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware and normalized to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _sanitize_csv_cell(value: object) -> str:
    """
    Sanitize values to prevent spreadsheet formula injection.
    Prefixes cell with a quote if string begins with =, +, -, @, tab, or CR.
    """
    if value is None:
        return ""
    str_val = str(value)
    if str_val and str_val[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{str_val}"
    return str_val


class ReportService:
    """Service generating project reports and analytics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.report_repo = ReportRepository(session)
        self.project_repo = ProjectRepository(session)
        self.scrum_service = ScrumService(session)

    async def _verify_project(self, project_id: uuid.UUID, org_id: uuid.UUID):
        """Verify project exists and belongs to the specified organization."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"project_id": str(project_id), "organization_id": str(org_id)},
            )
        return project

    # -------------------------------------------------------------
    # 1. Project Overview Report
    # -------------------------------------------------------------

    async def get_project_overview(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        due_window_days: int = 7,
    ) -> ProjectOverviewReportResponse:
        """
        Generate project overview health metrics, progress, and status distributions.
        """
        await self._verify_project(project_id, org_id)

        now_utc = datetime.now(UTC)
        due_window_end = now_utc + timedelta(days=due_window_days)

        # 1. Fetch high-level task metrics in single SQL aggregate
        aggregates = await self.report_repo.get_overview_task_aggregates(
            project_id=project_id,
            now_utc=now_utc,
            due_window_end=due_window_end,
        )

        total_tasks = aggregates["total_tasks"]
        completed_tasks = aggregates["completed_tasks"]

        # Completion percentage with zero denominator safeguard
        completion_pct = (
            round((completed_tasks / total_tasks) * 100, 1) if total_tasks > 0 else 0.0
        )

        # 2. Status distribution mapped against actual project workflow statuses
        wf_statuses = await self.report_repo.get_project_workflow_statuses(project_id)
        status_category_map: dict[str, str] = {}
        for st in wf_statuses:
            status_category_map[st.name.lower()] = st.category.lower()

        status_counts = await self.report_repo.get_distribution_by_status(project_id)
        status_items: list[StatusDistributionItem] = []

        in_progress_count = 0
        open_count = 0

        for status_name, count in status_counts:
            pct = round((count / total_tasks) * 100, 1) if total_tasks > 0 else 0.0
            cat = status_category_map.get(
                status_name.lower(),
                "done"
                if status_name.lower() in COMPLETED_STATUSES
                else "in_progress"
                if "progress" in status_name.lower() or "review" in status_name.lower()
                else "todo",
            )
            status_items.append(
                StatusDistributionItem(
                    status=status_name,
                    category=cat,
                    count=count,
                    percentage=pct,
                )
            )

            if cat == "done" or status_name.lower() in COMPLETED_STATUSES:
                pass
            elif cat == "in_progress":
                in_progress_count += count
            else:
                open_count += count

        # 3. Priority distribution
        priority_counts = await self.report_repo.get_distribution_by_priority(
            project_id
        )
        priority_items = [
            DistributionItem(
                name=p_name,
                count=count,
                percentage=round((count / total_tasks) * 100, 1)
                if total_tasks > 0
                else 0.0,
            )
            for p_name, count in priority_counts
        ]

        # 4. Type distribution
        type_counts = await self.report_repo.get_distribution_by_type(project_id)
        type_items = [
            DistributionItem(
                name=t_name,
                count=count,
                percentage=round((count / total_tasks) * 100, 1)
                if total_tasks > 0
                else 0.0,
            )
            for t_name, count in type_counts
        ]

        return ProjectOverviewReportResponse(
            project_id=project_id,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            open_tasks=open_count,
            in_progress_tasks=in_progress_count,
            overdue_tasks=aggregates["overdue_tasks"],
            unassigned_tasks=aggregates["unassigned_tasks"],
            completion_percentage=completion_pct,
            due_soon_tasks=aggregates["due_soon_tasks"],
            due_window_days=due_window_days,
            total_estimated_points=aggregates["total_points"],
            completed_estimated_points=aggregates["completed_points"],
            status_distribution=status_items,
            priority_distribution=priority_items,
            type_distribution=type_items,
            generated_at=now_utc,
        )

    # -------------------------------------------------------------
    # 2. Completion Trends Report
    # -------------------------------------------------------------

    async def get_completion_trends(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
        interval: Literal["day", "week", "month"] = "day",
    ) -> CompletionTrendsReportResponse:
        """
        Generate time-series completion trends partitioned into uniform date buckets.
        Validates date ranges and returns predictable intervals including zero buckets.
        """
        await self._verify_project(project_id, org_id)

        norm_start = _ensure_utc(start_date)
        norm_end = _ensure_utc(end_date)
        if norm_start is None or norm_end is None or norm_end < norm_start:
            raise DomainException(
                code="INVALID_DATE_RANGE",
                message="end_date must be greater than or equal to start_date.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )

        # Enforce maximum query span (366 days)
        if (norm_end - norm_start).days > 366:
            raise DomainException(
                code="DATE_RANGE_TOO_LARGE",
                message="Reporting date range cannot exceed 366 days.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )

        # 1. Fetch raw completion events in range
        completed_events = await self.report_repo.get_completed_tasks_in_range(
            project_id=project_id,
            start_date=norm_start,
            end_date=norm_end,
        )

        # 2. Generate discrete contiguous interval buckets
        buckets = self._generate_trend_buckets(norm_start, norm_end, interval)

        # 3. Aggregate completion events into the corresponding interval buckets
        total_tasks_completed = len(completed_events)
        total_points_completed = 0.0

        for comp_time, points in completed_events:
            total_points_completed += points
            norm_event_time = _ensure_utc(comp_time)
            if norm_event_time is None:
                continue

            for b in buckets:
                if b.bucket_start <= norm_event_time <= b.bucket_end:
                    b.completed_tasks += 1
                    b.completed_story_points = round(
                        b.completed_story_points + points, 1
                    )
                    break

        return CompletionTrendsReportResponse(
            project_id=project_id,
            start_date=norm_start,
            end_date=norm_end,
            interval=interval,
            total_completed_in_period=total_tasks_completed,
            total_points_completed_in_period=round(total_points_completed, 1),
            buckets=buckets,
            generated_at=datetime.now(UTC),
        )

    def _generate_trend_buckets(
        self,
        start_date: datetime,
        end_date: datetime,
        interval: Literal["day", "week", "month"],
    ) -> list[TrendBucketItem]:
        """Generate uniform contiguous time buckets covering the entire date window."""
        buckets: list[TrendBucketItem] = []

        if interval == "day":
            curr = datetime(
                start_date.year, start_date.month, start_date.day, tzinfo=UTC
            )
            while curr <= end_date:
                b_end = curr + timedelta(
                    hours=23, minutes=59, seconds=59, microseconds=999999
                )
                buckets.append(
                    TrendBucketItem(
                        bucket_start=curr,
                        bucket_end=b_end,
                        label=curr.strftime("%Y-%m-%d"),
                        completed_tasks=0,
                        completed_story_points=0.0,
                    )
                )
                curr += timedelta(days=1)

        elif interval == "week":
            # Align to start of week (Monday)
            curr = datetime(
                start_date.year, start_date.month, start_date.day, tzinfo=UTC
            )
            curr = curr - timedelta(days=curr.weekday())
            while curr <= end_date:
                b_end = curr + timedelta(
                    days=6, hours=23, minutes=59, seconds=59, microseconds=999999
                )
                buckets.append(
                    TrendBucketItem(
                        bucket_start=curr,
                        bucket_end=b_end,
                        label=curr.strftime("%Y-W%W"),
                        completed_tasks=0,
                        completed_story_points=0.0,
                    )
                )
                curr += timedelta(days=7)

        elif interval == "month":
            # Step month by month
            curr_year = start_date.year
            curr_month = start_date.month
            end_year = end_date.year
            end_month = end_date.month

            while (curr_year < end_year) or (
                curr_year == end_year and curr_month <= end_month
            ):
                b_start = datetime(curr_year, curr_month, 1, tzinfo=UTC)
                if curr_month == 12:
                    next_month_start = datetime(curr_year + 1, 1, 1, tzinfo=UTC)
                else:
                    next_month_start = datetime(
                        curr_year, curr_month + 1, 1, tzinfo=UTC
                    )
                b_end = next_month_start - timedelta(microseconds=1)

                buckets.append(
                    TrendBucketItem(
                        bucket_start=b_start,
                        bucket_end=b_end,
                        label=b_start.strftime("%Y-%m"),
                        completed_tasks=0,
                        completed_story_points=0.0,
                    )
                )

                if curr_month == 12:
                    curr_year += 1
                    curr_month = 1
                else:
                    curr_month += 1

        return buckets

    # -------------------------------------------------------------
    # 3. Team Workload Report
    # -------------------------------------------------------------

    async def get_team_workload(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        completed_since: datetime | None = None,
    ) -> TeamWorkloadReportResponse:
        """
        Aggregate assigned, in-progress, overdue, and completed work per team member,
        along with unassigned work metrics.
        """
        await self._verify_project(project_id, org_id)

        now_utc = datetime.now(UTC)
        norm_completed_since = _ensure_utc(completed_since)

        # 1. Fetch memberships joined with users
        memberships = await self.report_repo.get_project_members_with_users(project_id)

        # 2. Fetch workload aggregates grouped by assignee_id
        workload_aggregates = await self.report_repo.get_team_workload_task_aggregates(
            project_id=project_id,
            now_utc=now_utc,
            completed_since=norm_completed_since,
        )

        # Map by assignee_id
        agg_map = {row["assignee_id"]: row for row in workload_aggregates}

        # 3. Unassigned work row (assignee_id is None)
        unassigned_raw = agg_map.get(None, {})
        unassigned_work = UnassignedWorkItem(
            unassigned_tasks=unassigned_raw.get("active_assigned", 0),
            unassigned_in_progress_tasks=unassigned_raw.get("in_progress", 0),
            unassigned_overdue_tasks=unassigned_raw.get("overdue", 0),
            unassigned_story_points=round(unassigned_raw.get("total_points", 0.0), 1),
        )

        # 4. Member workload list
        member_items: list[MemberWorkloadItem] = []
        for mem in memberships:
            m_user = mem.user
            row = agg_map.get(m_user.id, {})

            member_items.append(
                MemberWorkloadItem(
                    user=MemberWorkloadUser(
                        id=m_user.id,
                        email=m_user.email,
                        full_name=m_user.full_name,
                    ),
                    role=mem.role,
                    assigned_tasks=row.get("active_assigned", 0),
                    in_progress_tasks=row.get("in_progress", 0),
                    overdue_tasks=row.get("overdue", 0),
                    completed_tasks=row.get("completed", 0),
                    total_story_points=round(row.get("total_points", 0.0), 1),
                    completed_story_points=round(row.get("completed_points", 0.0), 1),
                )
            )

        return TeamWorkloadReportResponse(
            project_id=project_id,
            total_members=len(member_items),
            members=member_items,
            unassigned_work=unassigned_work,
            completed_since=norm_completed_since,
            generated_at=now_utc,
        )

    # -------------------------------------------------------------
    # 4. Sprint Analytics Report
    # -------------------------------------------------------------

    async def get_sprint_analytics(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID | None = None,
    ) -> SprintAnalyticsReportResponse:
        """
        Generate sprint analytics integrating burndown and velocity.
        """
        await self._verify_project(project_id, org_id)

        # 1. Fetch project velocity history across completed sprints
        velocity_resp = await self.scrum_service.get_project_velocity(
            org_id=org_id,
            project_id=project_id,
        )

        # 2. If sprint_id is provided, fetch sprint summary & burndown
        sprint_name = None
        sprint_status = None
        start_date = None
        end_date = None
        completed_at = None
        committed_pts = 0.0
        completed_pts = 0.0
        committed_tks = 0
        completed_tks = 0
        comp_percentage = 0.0
        burndown_points = []

        if sprint_id is not None:
            sprint_summary = await self.scrum_service.get_sprint_summary(
                org_id=org_id,
                project_id=project_id,
                sprint_id=sprint_id,
            )
            sprint_name = sprint_summary.sprint_name
            sprint_status = sprint_summary.status
            start_date = sprint_summary.start_date
            end_date = sprint_summary.end_date
            committed_pts = sprint_summary.total_estimated_points
            completed_pts = sprint_summary.completed_points
            committed_tks = sprint_summary.total_task_count
            completed_tks = sprint_summary.completed_task_count
            comp_percentage = sprint_summary.completion_percentage_points

            burndown_resp = await self.scrum_service.get_sprint_burndown(
                org_id=org_id,
                project_id=project_id,
                sprint_id=sprint_id,
            )
            burndown_points = burndown_resp.data_points

        return SprintAnalyticsReportResponse(
            project_id=project_id,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            status=sprint_status,
            start_date=start_date,
            end_date=end_date,
            completed_at=completed_at,
            committed_points=committed_pts,
            completed_points=completed_pts,
            committed_tasks=committed_tks,
            completed_tasks=completed_tks,
            completion_percentage=comp_percentage,
            burndown=burndown_points,
            velocity_history=velocity_resp.sprints,
            average_velocity=velocity_resp.average_velocity_points,
            average_velocity_points=velocity_resp.average_velocity_points,
            average_velocity_tasks=velocity_resp.average_velocity_tasks,
            generated_at=datetime.now(UTC),
        )

    # -------------------------------------------------------------
    # 5. CSV Export
    # -------------------------------------------------------------

    async def export_report_csv(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        report_type: Literal[
            "overview", "completion_trends", "team_workload", "sprint_summary"
        ],
        due_window_days: int = 7,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        interval: Literal["day", "week", "month"] = "day",
        completed_since: datetime | None = None,
        sprint_id: uuid.UUID | None = None,
    ) -> str:
        """
        Generate CSV export for the specified report type.
        Applies spreadsheet formula-injection defenses and UTF-8 encoding.
        """
        output = io.StringIO()
        # UTF-8 BOM prefix for Microsoft Excel compatibility
        output.write("\ufeff")
        writer = csv.writer(output)

        if report_type == "overview":
            overview = await self.get_project_overview(
                org_id=org_id,
                project_id=project_id,
                due_window_days=due_window_days,
            )
            writer.writerow(["Project Overview Report", str(overview.project_id)])
            writer.writerow(["Generated At (UTC)", overview.generated_at.isoformat()])
            writer.writerow([])
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Total Tasks", overview.total_tasks])
            writer.writerow(["Completed Tasks", overview.completed_tasks])
            writer.writerow(["Open Tasks", overview.open_tasks])
            writer.writerow(["In Progress Tasks", overview.in_progress_tasks])
            writer.writerow(["Overdue Tasks", overview.overdue_tasks])
            writer.writerow(["Unassigned Tasks", overview.unassigned_tasks])
            writer.writerow(
                ["Completion Percentage (%)", overview.completion_percentage]
            )
            writer.writerow(
                [f"Due Soon (Next {due_window_days} Days)", overview.due_soon_tasks]
            )
            writer.writerow(["Total Story Points", overview.total_estimated_points])
            writer.writerow(
                ["Completed Story Points", overview.completed_estimated_points]
            )
            writer.writerow([])
            writer.writerow(["Status Breakdown", "Category", "Count", "Percentage (%)"])
            for st in overview.status_distribution:
                writer.writerow(
                    [
                        _sanitize_csv_cell(st.status),
                        _sanitize_csv_cell(st.category),
                        st.count,
                        st.percentage,
                    ]
                )
            writer.writerow([])
            writer.writerow(["Priority Breakdown", "Count", "Percentage (%)"])
            for pr in overview.priority_distribution:
                writer.writerow(
                    [
                        _sanitize_csv_cell(pr.name),
                        pr.count,
                        pr.percentage,
                    ]
                )

        elif report_type == "completion_trends":
            now_utc = datetime.now(UTC)
            s_date = start_date or (now_utc - timedelta(days=30))
            e_date = end_date or now_utc
            trends = await self.get_completion_trends(
                org_id=org_id,
                project_id=project_id,
                start_date=s_date,
                end_date=e_date,
                interval=interval,
            )
            writer.writerow(["Completion Trends Report", str(trends.project_id)])
            writer.writerow(["Interval", interval])
            writer.writerow(["Start Date (UTC)", trends.start_date.isoformat()])
            writer.writerow(["End Date (UTC)", trends.end_date.isoformat()])
            writer.writerow(["Total Completed Tasks", trends.total_completed_in_period])
            writer.writerow(
                ["Total Completed Points", trends.total_points_completed_in_period]
            )
            writer.writerow([])
            writer.writerow(
                [
                    "Interval Label",
                    "Bucket Start (UTC)",
                    "Bucket End (UTC)",
                    "Completed Tasks",
                    "Completed Story Points",
                ]
            )
            for b in trends.buckets:
                writer.writerow(
                    [
                        _sanitize_csv_cell(b.label),
                        b.bucket_start.isoformat(),
                        b.bucket_end.isoformat(),
                        b.completed_tasks,
                        b.completed_story_points,
                    ]
                )

        elif report_type == "team_workload":
            workload = await self.get_team_workload(
                org_id=org_id,
                project_id=project_id,
                completed_since=completed_since,
            )
            writer.writerow(["Team Workload Report", str(workload.project_id)])
            writer.writerow(["Generated At (UTC)", workload.generated_at.isoformat()])
            writer.writerow(["Total Members", workload.total_members])
            writer.writerow([])
            writer.writerow(
                [
                    "Member Name",
                    "Email",
                    "Project Role",
                    "Active Assigned Tasks",
                    "In Progress Tasks",
                    "Overdue Tasks",
                    "Completed Tasks",
                    "Total Story Points",
                    "Completed Story Points",
                ]
            )
            for mem in workload.members:
                writer.writerow(
                    [
                        _sanitize_csv_cell(mem.user.full_name),
                        _sanitize_csv_cell(mem.user.email),
                        _sanitize_csv_cell(mem.role or "member"),
                        mem.assigned_tasks,
                        mem.in_progress_tasks,
                        mem.overdue_tasks,
                        mem.completed_tasks,
                        mem.total_story_points,
                        mem.completed_story_points,
                    ]
                )
            writer.writerow(
                [
                    "Unassigned Work",
                    "",
                    "",
                    workload.unassigned_work.unassigned_tasks,
                    workload.unassigned_work.unassigned_in_progress_tasks,
                    workload.unassigned_work.unassigned_overdue_tasks,
                    "",
                    workload.unassigned_work.unassigned_story_points,
                    "",
                ]
            )

        elif report_type == "sprint_summary":
            sprint_analytics = await self.get_sprint_analytics(
                org_id=org_id,
                project_id=project_id,
                sprint_id=sprint_id,
            )
            writer.writerow(
                ["Sprint Analytics Report", str(sprint_analytics.project_id)]
            )
            writer.writerow(["Average Velocity", sprint_analytics.average_velocity])
            writer.writerow([])
            if sprint_analytics.sprint_id:
                writer.writerow(
                    [
                        "Selected Sprint",
                        _sanitize_csv_cell(sprint_analytics.sprint_name),
                    ]
                )
                writer.writerow(["Status", sprint_analytics.status])
                writer.writerow(["Committed Points", sprint_analytics.committed_points])
                writer.writerow(["Completed Points", sprint_analytics.completed_points])
                writer.writerow(["Committed Tasks", sprint_analytics.committed_tasks])
                writer.writerow(["Completed Tasks", sprint_analytics.completed_tasks])
                writer.writerow(
                    [
                        "Completion Percentage (%)",
                        sprint_analytics.completion_percentage,
                    ]
                )
                writer.writerow([])
            writer.writerow(
                [
                    "Historical Sprint",
                    "Start Date",
                    "End Date",
                    "Completed At",
                    "Committed Points",
                    "Completed Points",
                    "Committed Tasks",
                    "Completed Tasks",
                ]
            )
            for sp in sprint_analytics.velocity_history:
                writer.writerow(
                    [
                        _sanitize_csv_cell(sp.sprint_name),
                        sp.start_date.isoformat() if sp.start_date else "",
                        sp.end_date.isoformat() if sp.end_date else "",
                        sp.completed_at.isoformat() if sp.completed_at else "",
                        sp.committed_points,
                        sp.completed_points,
                        sp.committed_tasks,
                        sp.completed_tasks,
                    ]
                )

        return output.getvalue()

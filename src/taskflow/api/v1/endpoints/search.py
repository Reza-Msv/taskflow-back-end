import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import get_current_active_user, get_task_service
from taskflow.domains.identity.models import User
from taskflow.domains.work_items.schemas import GlobalSearchResponse
from taskflow.domains.work_items.service import TaskService

router = APIRouter(prefix="/search", tags=["Global Search"])


@router.get(
    "",
    response_model=GlobalSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Global Entity Search",
    description=(
        "Search accessible tasks and projects matching query text across "
        "title, key, description, or issue key."
    ),
)
async def search_entities(
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=200,
            description="Search query string",
            examples=["refactor", "TF-101", "mobile"],
        ),
    ],
    type: Annotated[
        Literal["all", "tasks", "projects"],
        Query(description="Entity types to include in search results"),
    ] = "all",
    organization_id: Annotated[
        uuid.UUID | None,
        Query(description="Scope search to an organization"),
    ] = None,
    project_id: Annotated[
        uuid.UUID | None,
        Query(description="Scope search to a specific project"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum results per entity type"),
    ] = 20,
    offset: Annotated[
        int,
        Query(ge=0, description="Number of results to skip"),
    ] = 0,
) -> GlobalSearchResponse:
    """Execute global search across authorized tasks and projects."""
    return await task_service.global_search(
        user_id=current_user.id,
        query=q,
        search_type=type,
        organization_id=organization_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import get_current_active_user, get_saved_filter_service
from taskflow.domains.identity.models import User
from taskflow.domains.work_items.schemas import (
    SavedFilterCreateRequest,
    SavedFilterListResponse,
    SavedFilterResponse,
    SavedFilterUpdateRequest,
    WorkItemListResponse,
    WorkItemResponse,
)
from taskflow.domains.work_items.service import SavedFilterService

router = APIRouter(prefix="/saved-filters", tags=["Saved Filters & Views"])


@router.post(
    "",
    response_model=SavedFilterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Saved Filter",
    description=(
        "Save a persistent task filter/view configuration for the current user."
    ),
)
async def create_saved_filter(
    request: SavedFilterCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    filter_service: Annotated[
        SavedFilterService, Depends(get_saved_filter_service)
    ],
) -> SavedFilterResponse:
    """Create a new user-owned saved filter."""
    saved_filter = await filter_service.create_saved_filter(
        user_id=current_user.id,
        request=request,
    )
    return SavedFilterResponse.model_validate(saved_filter)


@router.get(
    "",
    response_model=SavedFilterListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Saved Filters",
    description="Retrieve all saved filters owned by the authenticated user.",
)
async def list_saved_filters(
    current_user: Annotated[User, Depends(get_current_active_user)],
    filter_service: Annotated[
        SavedFilterService, Depends(get_saved_filter_service)
    ],
    organization_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by organization scope"),
    ] = None,
    project_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by project scope"),
    ] = None,
) -> SavedFilterListResponse:
    """List current user's saved filters."""
    filters = await filter_service.list_saved_filters(
        user_id=current_user.id,
        organization_id=organization_id,
        project_id=project_id,
    )
    return SavedFilterListResponse(
        items=[SavedFilterResponse.model_validate(f) for f in filters],
        total=len(filters),
    )


@router.get(
    "/{filter_id}",
    response_model=SavedFilterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Saved Filter Details",
    description="Retrieve a single saved filter by ID. Enforces owner authorization.",
)
async def get_saved_filter(
    filter_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    filter_service: Annotated[
        SavedFilterService, Depends(get_saved_filter_service)
    ],
) -> SavedFilterResponse:
    """Retrieve saved filter by ID."""
    sf = await filter_service.get_saved_filter(
        filter_id=filter_id,
        user_id=current_user.id,
    )
    return SavedFilterResponse.model_validate(sf)


@router.patch(
    "/{filter_id}",
    response_model=SavedFilterResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Saved Filter",
    description="Update a saved filter. Enforces owner authorization.",
)
async def update_saved_filter(
    filter_id: uuid.UUID,
    request: SavedFilterUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    filter_service: Annotated[
        SavedFilterService, Depends(get_saved_filter_service)
    ],
) -> SavedFilterResponse:
    """Update saved filter owned by user."""
    sf = await filter_service.update_saved_filter(
        filter_id=filter_id,
        user_id=current_user.id,
        request=request,
    )
    return SavedFilterResponse.model_validate(sf)


@router.delete(
    "/{filter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Saved Filter",
    description="Delete a saved filter. Enforces owner authorization.",
)
async def delete_saved_filter(
    filter_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    filter_service: Annotated[
        SavedFilterService, Depends(get_saved_filter_service)
    ],
) -> None:
    """Delete saved filter owned by user."""
    await filter_service.delete_saved_filter(
        filter_id=filter_id,
        user_id=current_user.id,
    )


@router.get(
    "/{filter_id}/tasks",
    response_model=WorkItemListResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Saved Filter",
    description="Execute the criteria of a saved filter and return matching tasks.",
)
async def execute_saved_filter(
    filter_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    filter_service: Annotated[
        SavedFilterService, Depends(get_saved_filter_service)
    ],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Items per page")
    ] = 25,
) -> WorkItemListResponse:
    """Execute saved filter rules and return matching tasks."""
    tasks, total = await filter_service.execute_saved_filter(
        filter_id=filter_id,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return WorkItemListResponse(
        items=[WorkItemResponse.model_validate(t) for t in tasks],
        page=page,
        page_size=page_size,
        total=total,
    )

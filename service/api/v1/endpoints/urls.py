"""URL management endpoints — CRUD for short links."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from service.api.deps import check_rate_limit, get_current_user
from service.config import settings
from service.db.session import get_db
from service.models.user import User
from service.schemas.url import (
    ShortenRequest,
    URLListResponse,
    URLResponse,
    UpdateURLRequest,
    PaginationQueryParams
)
from service.services import url_service

router = APIRouter(prefix="/urls", tags=["urls"])


def _to_response(short_url, base_url: str) -> URLResponse:
    return URLResponse(
        id=str(short_url.id),
        short_code=short_url.short_code,
        short_url=url_service.build_short_url(short_url.short_code),
        original_url=short_url.original_url,
        click_count=short_url.click_count,
        expires_at=short_url.expires_at,
        created_at=short_url.created_at,
        is_active=short_url.is_active,
    )


@router.post(
    "",
    response_model=URLResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Shorten a URL",
    dependencies=[Depends(chec...
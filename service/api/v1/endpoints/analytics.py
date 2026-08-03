"""Analytics endpoint — aggregated click data for a short URL."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from service.api.deps import get_current_user
from service.db.session import get_db
from service.models.user import User
from service.schemas.analytics import AnalyticsResponse
from service.services import analytics_service, url_service

router = APIRouter(tags=["analytics"])


@router.get(
    "/urls/{url_id}/analytics",
    response_model=AnalyticsResponse,
    summary="Get aggregated click analytics for a short URL",
)
def get_analytics(
    url_id: str,
    from_date: datetime | None = Query(None, description="Filter clicks from this datetime (ISO 8601)"),
    to_date: datetime | None = Query(None, description="Filter clicks up to this datetime (ISO 8601)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsResponse:
    """
    Return click analytics for the specified short URL.

    Includes total clicks, unique visitors, and breakdowns by date,
    country, device type, and referrer.
    """
    short_url = url_service.get_short_url_by_id(db, url_id=url_id, owner=current_user)
    if not short_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")

    data = analytics_service.get_analytics(
        db, short_url=short_url, from_date=from_date, to_date=to_date
    )
    return AnalyticsResponse(**data)

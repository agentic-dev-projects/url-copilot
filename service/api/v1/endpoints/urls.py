"""URL management endpoints — CRUD for short links."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKey

from service.api.deps import check_rate_limit, get_current_user
from service.config import settings
from service.db.session import get_db
from service.models.user import User
from service.schemas.url import (
    ShortenRequest,
    URLListResponse,
    URLResponse,
    UpdateURLRequest,
    QRResponseSchema
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
    dependencies=[Depends(check_rate_limit)],
)
def shorten_url(
    body: ShortenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> URLResponse:
    """Create a new short URL. Optionally set a custom alias and expiry."""
    try:
        short_url = url_service.create_short_url(
            db,
            owner=current_user,
            original_url=str(body.original_url),
            custom_alias=body.custom_alias,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return _to_response(short_url, settings.base_url)


@router.get(
    "",
    response_model=URLListResponse,
    summary="List all short URLs owned by the authenticated user",
)
def list_urls(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> URLListResponse:
    items, total = url_service.list_short_urls(db, owner=current_user, page=page, limit=limit, active_only=active_only)
    return URLListResponse(
        items=[_to_response(u, settings.base_url) for u in items],
        total=total,
        page=page,
        limit=limit,
    )


@router.get(
    "/{url_id}",
    response_model=URLResponse,
    summary="Get a single short URL by ID",
)
def get_url(
    url_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> URLResponse:
    short_url = url_service.get_short_url_by_id(db, url_id=url_id, owner=current_user)
    if not short_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")
    return _to_response(short_url, settings.base_url)


@router.put(
    "/{url_id}",
    response_model=URLResponse,
    summary="Update destination URL or expiry of an existing short link",
)
def update_url(
    url_id: str,
    body: UpdateURLRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> URLResponse:
    short_url = url_service.get_short_url_by_id(db, url_id=url_id, owner=current_user)
    if not short_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")

    try:
        short_url = url_service.update_short_url(
            db,
            short_url=short_url,
            original_url=str(body.original_url) if body.original_url else None,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return _to_response(short_url, settings.base_url)


@router.delete(
    "/{url_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a short URL",
)
def delete_url(
    url_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    short_url = url_service.get_short_url_by_id(db, url_id=url_id, owner=current_user)
    if not short_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")
    url_service.delete_short_url(db, short_url=short_url)


@router.get(
    "/{url_id}/qr",
    response_model=QRResponseSchema,
    summary="Generate and return a QR code for the original URL associated with the given short URL ID",
    dependencies=[Depends(check_rate_limit)],
)
def generate_qr_code(
    url_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    api_key: APIKey = Depends(get_current_user)
) -> JSONResponse:
    short_url = url_service.get_short_url_by_id(db, url_id=url_id, owner=current_user)
    if not short_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")
    try:
        qr_image = url_service.generate_qr_code(short_url.original_url)
        qr_code_data_uri = f"data:image/png;base64,{qr_image.read().encode('base64').decode()}"
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return JSONResponse(content={"qr_code_url": qr_code_data_uri})

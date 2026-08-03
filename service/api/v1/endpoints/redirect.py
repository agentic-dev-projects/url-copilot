"""Redirect endpoint — the hot path that resolves short codes to original URLs."""

import hashlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from service.db.session import get_db
from service.services import analytics_service, url_service
from starlette import status

router = APIRouter(tags=["redirect"])


@router.get(
    "/{short_code}",
    status_code=status.HTTP_302_FOUND,
    summary="Redirect a short code to its original URL",
    response_class=RedirectResponse,
)
def redirect(
    short_code: str,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Resolve `short_code` and redirect the client to the original URL.

    Returns:
    - 302 Found      — valid, active link
    - 404 Not Found  — unknown short code
    - 410 Gone       — link has expired
    """
    short_url = url_service.get_short_url_by_code(db, short_code=short_code)

    if short_url is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short URL not found")

    if url_service.is_expired(short_url):
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This link has expired")

    # Record the click — failures here must not block the redirect
    try:
        raw_ip = request.client.host if request.client else None
        ip_hash = hashlib.sha256(raw_ip.encode()).hexdigest() if raw_ip else None
        user_agent_str = request.headers.get("user-agent")
        device_type = _detect_device(user_agent_str)
        referrer = request.headers.get("referer")

        analytics_service.record_click(
            db,
            short_url=short_url,
            ip_hash=ip_hash,
            user_agent=user_agent_str,
            device_type=device_type,
            referrer=referrer,
            country_code=None,  # geo-lookup is out of scope for v1
        )
        url_service.increment_click_count(db, short_url=short_url)
    except Exception:
        pass  # analytics failure must never block a redirect

    return RedirectResponse(url=short_url.original_url, status_code=status.HTTP_302_FOUND)


def _detect_device(user_agent: str | None) -> str:
    """Classify a User-Agent string into a device category."""
    if not user_agent:
        return "unknown"
    try:
        from user_agents import parse
        ua = parse(user_agent)
        if ua.is_bot:
            return "bot"
        if ua.is_mobile:
            return "mobile"
        if ua.is_tablet:
            return "tablet"
        return "desktop"
    except Exception:
        return "unknown"

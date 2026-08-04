"""
FastAPI shared dependencies — injected into route handlers via Depends().

Provides:
- get_current_user: authenticates the X-API-Key header and returns the User
- check_rate_limit: enforces per-key request rate before any business logic runs
"""

import hashlib

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from service.db.session import get_db
from service.models.user import User
from service.services.auth_service import get_user_by_api_key
from service.core.rate_limiter import is_rate_limited


def get_current_user(
    x_api_key: str | None = Header(default=None, description="API key in the format sk_<token>"),
    db: Session = Depends(get_db),
) -> User:
    """
    Authenticate the request via the X-API-Key header.

    Raises HTTP 401 if the key is missing, invalid, or belongs to an inactive user.
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is required",
        )
    user = get_user_by_api_key(db, raw_key=x_api_key)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
        )
    return user


def check_rate_limit(
    x_api_key: str = Header(..., description="API key used as rate limit identifier"),
) -> None:
    """
    Enforce per-key rate limiting before the route handler runs.

    Uses the first 16 chars of the key hash as the Redis identifier so the
    raw key is never written to cache.

    Raises HTTP 429 if the key has exceeded its request quota.
    """
    identifier = hashlib.sha256(x_api_key.encode()).hexdigest()[:16]
    if is_rate_limited(identifier):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before retrying.",
        )

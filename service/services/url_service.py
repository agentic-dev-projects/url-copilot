"""
URL service — core business logic for shortening, redirecting, and managing links.

Route handlers delegate all logic here; this module has no FastAPI imports.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from service.config import settings
from service.core.url_generator import generate_short_code, is_valid_custom_alias
from service.models.short_url import ShortURL
from service.models.user import User


# ── Validation ────────────────────────────────────────────────────────────────

def is_valid_url(url: str) -> bool:
    """Return True if `url` has a valid http/https scheme and a non-empty host."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


# ── Write operations ──────────────────────────────────────────────────────────

def create_short_url(
    db: Session,
    owner: User,
    original_url: str,
    custom_alias: str | None = None,
    expires_at: datetime | None = None,
) -> ShortURL:
    """
    Shorten `original_url` and persist the mapping.

    Raises:
        ValueError — invalid URL, invalid alias format, or alias already taken.
    """
    if not is_valid_url(original_url):
        raise ValueError(f"Invalid URL: {original_url}")

    if custom_alias is not None:
        if not is_valid_custom_alias(custom_alias):
            raise ValueError(f"Invalid alias format: {custom_alias}")
        if db.query(ShortURL).filter(ShortURL.short_code == custom_alias).first():
            raise ValueError(f"Alias already taken: {custom_alias}")
        short_code = custom_alias
    else:
        short_code = _generate_unique_code(db)

    short_url = ShortURL(
        short_code=short_code,
        original_url=original_url,
        owner_id=owner.id,
        expires_at=expires_at,
    )
    db.add(short_url)
    db.commit()
    db.refresh(short_url)
    return short_url


def update_short_url(
    db: Session,
    short_url: ShortURL,
    original_url: str | None = None,
    expires_at: datetime | None = None,
) -> ShortURL:
    """Update destination URL and/or expiry on an existing short link."""
    if original_url is not None:
        if not is_valid_url(original_url):
            raise ValueError(f"Invalid URL: {original_url}")
        short_url.original_url = original_url

    if expires_at is not None:
        short_url.expires_at = expires_at

    db.commit()
    db.refresh(short_url)
    return short_url


def delete_short_url(db: Session, short_url: ShortURL) -> None:
    """Soft-delete a short link (preserves analytics history)."""
    short_url.is_active = False
    db.commit()


# ── Read operations ───────────────────────────────────────────────────────────

def get_short_url_by_code(db: Session, short_code: str) -> ShortURL | None:
    """Return the active ShortURL for `short_code`, or None if not found."""
    return (
        db.query(ShortURL)
        .filter(ShortURL.short_code == short_code, ShortURL.is_active == True)  # noqa: E712
        .first()
    )


def get_short_url_by_id(db: Session, url_id: str, owner: User) -> ShortURL | None:
    """Return a ShortURL owned by `owner`, or None if not found / not owner."""
    import uuid as _uuid
    try:
        parsed_id = _uuid.UUID(url_id)
    except ValueError:
        return None
    return (
        db.query(ShortURL)
        .filter(
            ShortURL.id == parsed_id,
            ShortURL.owner_id == owner.id,
            ShortURL.is_active == True,  # noqa: E712
        )
        .first()
    )


def list_short_urls(
    db: Session, owner: User, page: int = 1, limit: int = 20, active_only: bool = True
) -> tuple[list[ShortURL], int]:
    """
    Return a paginated list of URLs owned by `owner`.

    Returns:
        (items, total_count)
    """
    query = db.query(ShortURL).filter(ShortURL.owner_id == owner.id)
    if active_only:
        query = query.filter(ShortURL.is_active == True)  # noqa: E712
    total = query.count()
    items = query.order_by(ShortURL.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return items, total


def is_expired(short_url: ShortURL) -> bool:
    """Return True if the link has passed its expiry datetime."""
    if short_url.expires_at is None:
        return False
    return datetime.now(timezone.utc) > short_url.expires_at


def build_short_url(short_code: str) -> str:
    """Construct the fully-qualified short URL from a short code."""
    return f"{settings.base_url.rstrip('/')}/{short_code}"


def increment_click_count(db: Session, short_url: ShortURL) -> None:
    """Atomically increment the denormalized click counter."""
    short_url.click_count += 1
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_unique_code(db: Session, max_attempts: int = 5) -> str:
    """Generate a short code that doesn't already exist in the DB."""
    for _ in range(max_attempts):
        code = generate_short_code(length=settings.short_code_length)
        if not db.query(ShortURL).filter(ShortURL.short_code == code).first():
            return code
    raise RuntimeError("Failed to generate a unique short code after max attempts")

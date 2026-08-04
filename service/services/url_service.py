"""
URL service — core business logic for shortening, redirecting, and managing links.

Route handlers delegate all logic here; this module has no FastAPI imports.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse
import qrcode
from io import BytesIO

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
            raise ValueError(f"Invalid alias format: {cu...
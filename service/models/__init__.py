"""
Model registry — import all ORM models here.

This ensures that SQLAlchemy's metadata and Alembic's autogenerate can
discover every table without each file needing to import the others.

Any new model module must be added to this file.
"""

from service.models.api_key import APIKey  # noqa: F401
from service.models.click_event import ClickEvent  # noqa: F401
from service.models.short_url import ShortURL  # noqa: F401
from service.models.user import User  # noqa: F401

__all__ = ["User", "APIKey", "ShortURL", "ClickEvent"]

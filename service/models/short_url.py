"""
ShortURL ORM model.

Core entity that maps a short code to an original long URL.

Design notes:
- `short_code` is indexed for O(1) redirect lookups
- `click_count` is a denormalized counter updated asynchronously — avoids
  an expensive COUNT(*) on click_events for every dashboard request
- Soft-delete via `is_active` preserves analytics history for deleted links
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from service.db.base import Base


class ShortURL(Base):
    __tablename__ = "short_urls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The short code used in redirect URLs (e.g. "abc123" or custom alias "my-campaign")
    short_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Null means the link never expires
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Denormalized for fast reads — updated async after each click
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Soft-delete: analytics history is preserved when a link is "deleted"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    owner: Mapped["User"] = relationship("User", back_populates="short_urls")  # noqa: F821
    click_events: Mapped[list["ClickEvent"]] = relationship(  # noqa: F821
        "ClickEvent", back_populates="short_url", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ShortURL code={self.short_code} clicks={self.click_count}>"

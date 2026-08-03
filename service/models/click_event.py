"""
ClickEvent ORM model.

Records each individual visit (click) on a short URL for analytics.

Privacy notes:
- Raw IP addresses are never stored; only a SHA-256 hash is persisted.
  This allows unique-visitor counting without retaining PII.
- `device_type` is derived from the User-Agent header at write time,
  so the raw User-Agent can be omitted after processing if desired.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from service.db.base import Base


class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    short_url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("short_urls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    # SHA-256 of the visitor's IP — PII-safe, supports unique visitor counts
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Raw User-Agent string for debugging; device_type is the derived field used in reports
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Derived from user_agent: "desktop" | "mobile" | "tablet" | "bot" | "unknown"
    device_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # HTTP Referer header (e.g. "twitter.com", "direct" if absent)
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ISO 3166-1 alpha-2 country code derived from IP geo-lookup
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    short_url: Mapped["ShortURL"] = relationship(  # noqa: F821
        "ShortURL", back_populates="click_events"
    )

    def __repr__(self) -> str:
        return f"<ClickEvent short_url_id={self.short_url_id} at={self.clicked_at}>"

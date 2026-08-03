"""
APIKey ORM model.

Stores a hashed API key per user. The raw key is shown once at creation and
never persisted — only the SHA-256 hash is stored for verification.

Key rotation is non-destructive: old keys are deactivated (is_active=False)
and a new record is inserted, preserving the audit trail.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from service.db.base import Base


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SHA-256 hex digest of the raw key — never store plaintext
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # First 8 chars of the raw key displayed to users for identification ("sk_abc123...")
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # False after rotation — keeps history without exposing old keys
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped["User"] = relationship("User", back_populates="api_keys")  # noqa: F821

    def __repr__(self) -> str:
        return f"<APIKey prefix={self.key_prefix} user_id={self.user_id} active={self.is_active}>"

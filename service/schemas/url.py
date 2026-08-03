"""
Pydantic schemas for URL management endpoints.

Defines request bodies and response shapes for:
- POST   /api/v1/urls         (shorten)
- GET    /api/v1/urls         (list)
- GET    /api/v1/urls/{id}    (detail)
- PUT    /api/v1/urls/{id}    (update)
- DELETE /api/v1/urls/{id}    (delete — no body)
"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ShortenRequest(BaseModel):
    original_url: HttpUrl = Field(..., description="The long URL to shorten")
    custom_alias: str | None = Field(
        None,
        min_length=3,
        max_length=32,
        description="Optional custom slug (alphanumeric + hyphens, no leading/trailing hyphens)",
    )
    expires_at: datetime | None = Field(
        None, description="Optional expiry in ISO 8601 format; null means never expires"
    )

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_future(cls, v: datetime | None) -> datetime | None:
        if v is not None and v <= datetime.now(tz=v.tzinfo):
            raise ValueError("expires_at must be in the future")
        return v


class URLResponse(BaseModel):
    id: str
    short_code: str
    short_url: str = Field(..., description="Fully-qualified short URL including base domain")
    original_url: str
    click_count: int
    expires_at: datetime | None
    created_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class UpdateURLRequest(BaseModel):
    original_url: HttpUrl | None = Field(None, description="New destination URL")
    expires_at: datetime | None = Field(None, description="New expiry datetime; null removes expiry")

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_future(cls, v: datetime | None) -> datetime | None:
        if v is not None and v <= datetime.now(tz=v.tzinfo):
            raise ValueError("expires_at must be in the future")
        return v


class URLListResponse(BaseModel):
    items: list[URLResponse]
    total: int
    page: int
    limit: int

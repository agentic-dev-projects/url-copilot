"""
Pydantic schemas for URL management endpoints.

Defines request bodies and response shapes for:
- POST   /api/v1/urls         (shorten)
- GET    /api/v1/urls         (list)
- GET    /api/v1/urls/{id}    (detail)
- PUT    /api/v1/urls/{id}    (update)
- DELETE /api/v1/urls/{id}    (delete — no body)
- GET    /api/v1/urls/{id}/qr (QR code)
"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_validator


class PaginationQueryParams(BaseModel):
    skip: int = Field(default=0, description="Number of records to skip for pagination")
    limit: int = Field(default=10, ge=1, le=100, description="Limit on the number of records to return")


class QRResponseSchema(BaseModel):
    """Response schema for the QR code endpoint."""
    qr_code_url: str = Field(..., description="URL or base64 data URI of the generated QR code image")


class ShortenRequest(BaseModel):
    original_url: HttpUrl = Field(..., description="The long URL to shorten")
    custom_alias: str | None = Field(
        None,
        min_length=3,
        max_length=32,
        description="Optional custom...
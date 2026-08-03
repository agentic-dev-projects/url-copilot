"""
Pydantic schemas for analytics endpoints.

Defines the response shape for:
- GET /api/v1/urls/{id}/analytics
"""

from pydantic import BaseModel, Field


class ClicksByDate(BaseModel):
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    count: int


class ClicksByCountry(BaseModel):
    country_code: str = Field(..., description="ISO 3166-1 alpha-2 country code")
    count: int


class ClicksByDevice(BaseModel):
    device_type: str = Field(..., description="desktop | mobile | tablet | bot | unknown")
    count: int


class TopReferrer(BaseModel):
    referrer: str = Field(..., description="Referrer domain or 'direct'")
    count: int


class AnalyticsResponse(BaseModel):
    short_url_id: str
    short_code: str
    total_clicks: int
    unique_clicks: int = Field(..., description="Distinct IP hashes — approximate unique visitors")
    clicks_by_date: list[ClicksByDate]
    clicks_by_country: list[ClicksByCountry]
    clicks_by_device: list[ClicksByDevice]
    top_referrers: list[TopReferrer]

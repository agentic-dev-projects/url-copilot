"""
Pydantic schemas for authentication endpoints.

Defines request bodies and response shapes for:
- POST /api/v1/auth/register
- POST /api/v1/auth/rotate-key
"""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address")


class RegisterResponse(BaseModel):
    user_id: str = Field(..., description="UUID of the newly created user")
    api_key: str = Field(..., description="Raw API key — shown once, store securely")
    key_prefix: str = Field(..., description="First 8 chars of the key for identification")


class RotateKeyResponse(BaseModel):
    api_key: str = Field(..., description="New raw API key — shown once, store securely")
    key_prefix: str = Field(..., description="First 8 chars of the new key")

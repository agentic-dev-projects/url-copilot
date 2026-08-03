"""API v1 router — registers all endpoint groups under /api/v1."""

from fastapi import APIRouter

from service.api.v1.endpoints import analytics, auth, redirect, urls

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(urls.router)
api_router.include_router(analytics.router)

# Redirect lives at the root (/{short_code}), registered separately in main.py
redirect_router = redirect.router

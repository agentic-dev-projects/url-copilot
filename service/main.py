"""
url-copilot — FastAPI application entry point.

Initialises the app, registers middleware, mounts versioned API routers,
and exposes infrastructure endpoints (health check).

Run locally:
    uvicorn service.main:app --reload

Run in Docker:
    docker compose up
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from service.config import settings

app = FastAPI(
    title=settings.app_name,
    description="AI-powered URL shortener — shorten links, track clicks, evolve with AI.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Infrastructure endpoints ──────────────────────────────────────────────────
# Registered before the redirect router so /health is not caught by /{short_code}
@app.get("/health", tags=["infrastructure"], summary="Liveness probe")
def health_check() -> dict:
    """
    Returns 200 if the application process is running.

    Does NOT verify database or cache connectivity — use a readiness probe
    (to be added) for deep health checks before routing traffic.
    """
    return {"status": "healthy"}


# ── Routers ───────────────────────────────────────────────────────────────────
from service.api.v1.router import api_router, redirect_router

app.include_router(api_router)
# Redirect router is last — /{short_code} must not shadow any fixed paths above
app.include_router(redirect_router)

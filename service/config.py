"""
Application configuration.

All settings are loaded from environment variables (or a .env file).
Import the module-level `settings` singleton everywhere configuration is needed
— never instantiate Settings directly.

Usage:
    from service.config import settings
    print(settings.base_url)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently drop unknown env vars
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_name: str = "url-copilot"
    base_url: str = "http://localhost:8000"
    debug: bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql://postgres:password@localhost:5432/urlcopilot"

    # ── Cache ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Security ──────────────────────────────────────────────────────────────
    # Number of random bytes used when generating an API key
    api_key_bytes: int = 32

    # ── Rate limiting ─────────────────────────────────────────────────────────
    rate_limit_per_minute: int = 60

    # ── Short code ────────────────────────────────────────────────────────────
    short_code_length: int = 6


# Module-level singleton — import this, don't instantiate Settings elsewhere
settings = Settings()

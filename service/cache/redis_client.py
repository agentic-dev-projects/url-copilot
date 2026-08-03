"""
Redis client — connection singleton and helpers.

Provides a module-level Redis client used by the rate limiter and redirect cache.
The client is initialised lazily on first use to avoid import-time side effects.

Usage:
    from service.cache.redis_client import get_redis

    redis = get_redis()
    redis.set("key", "value", ex=60)
"""

import redis as redis_lib

from service.config import settings

_client: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis:
    """Return the module-level Redis client, initialising it on first call."""
    global _client
    if _client is None:
        _client = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _client

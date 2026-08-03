"""
Rate limiter — fixed-window counter backed by Redis.

Each API key gets a counter key that expires after 60 seconds.
On every request the counter is incremented; if it exceeds the limit
the request is rejected with HTTP 429.

Usage:
    from service.core.rate_limiter import is_rate_limited

    if is_rate_limited(api_key_prefix):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
"""

from service.cache.redis_client import get_redis
from service.config import settings

_KEY_TTL_SECONDS = 60
_KEY_PREFIX = "rl:"


def is_rate_limited(identifier: str) -> bool:
    """
    Return True if `identifier` has exceeded the configured request rate.

    Uses an atomic Redis INCR + EXPIRE pattern:
    1. Increment the counter for this window.
    2. Set TTL on first increment so the key auto-expires after the window.
    3. If the counter exceeds the limit, the request is denied.

    `identifier` is typically the API key prefix or hashed key so that
    the raw key is never written to Redis.
    """
    redis = get_redis()
    redis_key = f"{_KEY_PREFIX}{identifier}"

    count = redis.incr(redis_key)
    if count == 1:
        # First request in this window — set the expiry
        redis.expire(redis_key, _KEY_TTL_SECONDS)

    return count > settings.rate_limit_per_minute


def get_remaining_requests(identifier: str) -> int:
    """Return how many requests remain in the current window for `identifier`."""
    redis = get_redis()
    redis_key = f"{_KEY_PREFIX}{identifier}"
    current = redis.get(redis_key)
    if current is None:
        return settings.rate_limit_per_minute
    used = int(current)
    return max(0, settings.rate_limit_per_minute - used)

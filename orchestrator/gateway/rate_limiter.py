"""
RateLimiter — per-user call rate enforcement using an in-memory sliding window.

Limit: 20 calls per 60-second window per user (github_login).

Why in-memory and not Redis?
-----------------------------
For a single-process prototype running on one machine, in-memory is sufficient
and has zero infrastructure dependency.  The tradeoff is that the counter resets
on process restart and does not work across multiple processes/instances.

In production, replace the internal dict with a Redis INCR + EXPIRE or a Redis
sorted set sliding window — the RateLimiter interface (check()) stays the same.

Sliding window algorithm
------------------------
For each user, a list of call timestamps (epoch seconds) is maintained.
On every check():
  1. Remove timestamps older than 60 seconds (outside the window).
  2. If len(remaining) >= LIMIT: raise RateLimitError.
  3. Append the current timestamp.

This is exact (not approximate like a fixed window counter) and O(n) where n
is calls in the last 60 seconds — typically tiny.
"""

import time

from orchestrator.gateway.auth import CurrentUser
from orchestrator.gateway.models import RateLimitError

_WINDOW_SECONDS = 60
_MAX_CALLS = 20


class RateLimiter:
    """Per-user call rate limiter using an in-memory sliding window."""

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = {}   # github_login → [timestamp, ...]

    def check(self, user: CurrentUser) -> None:
        """Assert the user has not exceeded 20 calls in the last 60 seconds.

        Appends the current timestamp to the user's window on success.

        Args:
            user: Resolved CurrentUser from TokenAuthenticator.

        Raises:
            RateLimitError: if the user has reached the per-minute call limit.
        """
        now = time.monotonic()
        login = user.github_login

        window = self._windows.get(login, [])
        # Evict timestamps outside the sliding window
        window = [ts for ts in window if now - ts < _WINDOW_SECONDS]

        if len(window) >= _MAX_CALLS:
            raise RateLimitError(
                f"Rate limit exceeded for '{login}': {_MAX_CALLS} calls per "
                f"{_WINDOW_SECONDS}s. Retry after the window expires."
            )

        window.append(now)
        self._windows[login] = window

    def reset(self, github_login: str) -> None:
        """Clear the rate limit window for a user.  Intended for tests only."""
        self._windows.pop(github_login, None)

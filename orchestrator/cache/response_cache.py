"""
ResponseCache — 24-hour SHA-256-keyed LLM response cache backed by orch_cache.

Why cache LLM responses?
------------------------
gpt-4o calls for architecture design cost ~$0.05–0.15 per invocation.  A re-run
of the same requirement (transient failure recovery, dev iteration) would hit
OpenAI again for identical output.  The cache short-circuits that with a hash
lookup in the same PostgreSQL database — under 1ms vs. 1–10 seconds for a real
LLM call.

Cache key
---------
SHA-256(prompt_text + model) — deterministic hash over the full prompt string
concatenated with the model name.  Identical prompts sent to different models
produce different cache entries because gpt-4o and o1-mini generate different output.

TTL
---
24 hours (configurable via ttl_hours parameter on set()).  Architectural decisions
don't change within a day; the default TTL covers re-runs in development while
being short enough to pick up prompt improvements in CI.

Upsert semantics
----------------
set() uses INSERT ... ON CONFLICT (prompt_hash) DO UPDATE to refresh the TTL
and response when the same prompt is re-cached (e.g. after a prompt version bump).
hit_count is reset to 0 on refresh so usage statistics reflect the current entry.

SQLite compatibility
--------------------
Expiry checking is done in Python (_is_expired helper) rather than using
database-specific datetime functions (PostgreSQL: now(), SQLite: datetime('now')).
This lets the same code path run against both engines — production uses PostgreSQL,
unit tests use SQLite in-memory.  expires_at is stored as an ISO-8601 string,
which sorts lexicographically for UTC timestamps.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator

from sqlalchemy import text
from sqlalchemy.orm import Session

from service.db.session import SessionLocal

_DEFAULT_TTL_HOURS = 24


class ResponseCache:
    """SHA-256-keyed LLM response cache with configurable TTL."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, prompt_text: str, model: str) -> dict | None:
        """Return a cached response if one exists and has not expired.

        Args:
            prompt_text: Full prompt string (all messages concatenated).
            model:       Model name (e.g. "gpt-4o").

        Returns:
            Cached response dict or None on a miss / expired entry.

        Side effect:
            Increments hit_count by 1 on a valid cache hit.
        """
        prompt_hash = self._hash(prompt_text, model)
        row = self.session.execute(
            text(
                "SELECT response, expires_at FROM orch_cache "
                "WHERE prompt_hash = :hash"
            ),
            {"hash": prompt_hash},
        ).mappings().one_or_none()

        if row is None:
            return None
        if self._is_expired(row["expires_at"]):
            return None

        self.session.execute(
            text(
                "UPDATE orch_cache SET hit_count = hit_count + 1 "
                "WHERE prompt_hash = :hash"
            ),
            {"hash": prompt_hash},
        )
        self.session.commit()

        response = row["response"]
        if isinstance(response, str):
            response = json.loads(response)
        return response

    def set(
        self,
        prompt_text: str,
        model: str,
        response: dict,
        ttl_hours: int = _DEFAULT_TTL_HOURS,
    ) -> None:
        """Insert or refresh a cache entry.

        Uses INSERT ... ON CONFLICT DO UPDATE so calling set() twice with the
        same prompt refreshes the TTL rather than raising a unique constraint error.

        Args:
            prompt_text: The full prompt string used to generate the response.
            model:       The model name.
            response:    Response dict to cache (JSON-serialised for storage).
            ttl_hours:   Time-to-live in hours (default 24).  Pass a negative
                         value in tests to create an already-expired entry.
        """
        prompt_hash = self._hash(prompt_text, model)
        cache_id = str(uuid.uuid4())
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        ).isoformat()
        response_json = json.dumps(response)

        self.session.execute(
            text(
                "INSERT INTO orch_cache "
                "(id, prompt_hash, model_used, response, hit_count, expires_at) "
                "VALUES (:id, :hash, :model, :response, 0, :expires_at) "
                "ON CONFLICT (prompt_hash) DO UPDATE SET "
                "    model_used = excluded.model_used, "
                "    response   = excluded.response, "
                "    hit_count  = 0, "
                "    expires_at = excluded.expires_at"
            ),
            {
                "id": cache_id,
                "hash": prompt_hash,
                "model": model,
                "response": response_json,
                "expires_at": expires_at,
            },
        )
        self.session.commit()

    def invalidate(self, prompt_text: str, model: str) -> None:
        """Hard-delete a cache entry by prompt + model."""
        self.session.execute(
            text("DELETE FROM orch_cache WHERE prompt_hash = :hash"),
            {"hash": self._hash(prompt_text, model)},
        )
        self.session.commit()

    def hit_count(self, prompt_text: str, model: str) -> int:
        """Return the hit_count for an entry, or 0 if not found."""
        row = self.session.execute(
            text("SELECT hit_count FROM orch_cache WHERE prompt_hash = :hash"),
            {"hash": self._hash(prompt_text, model)},
        ).mappings().one_or_none()
        return int(row["hit_count"]) if row else 0

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _hash(prompt_text: str, model: str) -> str:
        payload = (prompt_text + model).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _is_expired(expires_at: Any) -> bool:
        if isinstance(expires_at, datetime):
            dt = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(expires_at))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt <= datetime.now(timezone.utc)


# ── module-level convenience ─────────────────────────────────────────────────


@contextmanager
def make_response_cache() -> Generator[ResponseCache, None, None]:
    """Create a ResponseCache backed by a fresh SessionLocal session.

    Usage:
        with make_response_cache() as cache:
            result = cache.get(full_prompt, "gpt-4o")
    """
    session = SessionLocal()
    try:
        yield ResponseCache(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

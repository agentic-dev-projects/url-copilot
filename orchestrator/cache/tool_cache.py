"""
ToolCache — in-memory per-run tool result cache.

Motivation
----------
Stage agents call tools in a multi-turn loop (Phase 12 — StageAgent).  Some
calls are expensive: read_file() hits the filesystem, run_tests() runs the full
pytest suite, get_pr_diff() calls the GitHub API.  If the LLM emits the same
tool call in consecutive turns (re-reading a file for context is common), ToolCache
avoids the duplicate work within the same run.

Cache key
---------
SHA-256(tool_name + json.dumps(args, sort_keys=True)) — stable regardless of
how the LLM orders keys in the arguments dict.

Lifecycle
---------
ToolCache is scoped to a single orchestrator run and lives entirely in memory.
OrchestratorState carries a `tool_cache: dict` field so the cache survives
LangGraph node transitions within the same run:

    # In StageAgent (Phase 12):
    cache = ToolCache(state.get("tool_cache", {}))
    result = cache.get("read_file", {"path": "service/api/endpoints.py"})
    if result is None:
        result = filesystem.read_file("service/api/endpoints.py")
        cache.set("read_file", {"path": "service/api/endpoints.py"}, result)
    state["tool_cache"] = cache.to_dict()   # persist back into state

The cache is intentionally NOT written to the database.  Losing it on a process
restart just means the next run re-executes the tool calls, which is correct
behaviour — tool results may have changed (e.g. a test that was failing is now
passing).
"""

import hashlib
import json
from typing import Any


class ToolCache:
    """SHA-256-keyed in-memory cache for tool call results within a single run."""

    def __init__(self, initial: dict | None = None) -> None:
        """
        Args:
            initial: Existing cache dict from OrchestratorState['tool_cache'],
                     or None to start fresh.
        """
        self._store: dict[str, Any] = dict(initial) if initial else {}

    def get(self, tool_name: str, args: dict) -> Any | None:
        """Return the cached result for this tool + args combination, or None.

        Args:
            tool_name: The tool function name (e.g. "read_file").
            args:      The exact arguments dict passed to the tool.

        Returns:
            Cached result, or None on a cache miss.
        """
        return self._store.get(self._key(tool_name, args))

    def set(self, tool_name: str, args: dict, result: Any) -> None:
        """Store a tool result.

        Args:
            tool_name: The tool function name.
            args:      The arguments dict passed to the tool.
            result:    The tool's return value.
        """
        self._store[self._key(tool_name, args)] = result

    def invalidate(self, tool_name: str, args: dict) -> None:
        """Remove one entry from the cache.

        Use this after a write operation that would invalidate a prior read
        (e.g., write_file() should invalidate the cached read_file() result
        for the same path).
        """
        self._store.pop(self._key(tool_name, args), None)

    def clear(self) -> None:
        """Evict all entries.  Called when transitioning to a new stage to prevent
        stale reads from bleeding across stage boundaries."""
        self._store.clear()

    def size(self) -> int:
        """Return the number of cached entries."""
        return len(self._store)

    def to_dict(self) -> dict:
        """Return a copy of the internal store for OrchestratorState['tool_cache']."""
        return dict(self._store)

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _key(tool_name: str, args: dict) -> str:
        payload = tool_name + json.dumps(args, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

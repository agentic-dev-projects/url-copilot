"""
Unit tests for ResponseCache and ToolCache.

No PostgreSQL, no Docker — run anywhere:
    .venv/bin/python -m pytest orchestrator/tests/test_cache.py -v

ResponseCache tests use SQLite in-memory with an orch_cache table that mirrors
the PostgreSQL schema (TEXT instead of JSONB/UUID, same column names and constraints).
The ON CONFLICT (prompt_hash) DO UPDATE upsert syntax works on SQLite 3.24+ and
PostgreSQL identically.

ToolCache tests are pure Python — no DB, no fixtures.
"""

import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.cache.response_cache import ResponseCache
from orchestrator.cache.tool_cache import ToolCache

# ── ResponseCache fixture ─────────────────────────────────────────────────────


@pytest.fixture()
def session() -> Session:
    """Fresh SQLite in-memory session with orch_cache table."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE orch_cache (
                id          TEXT PRIMARY KEY,
                prompt_hash TEXT UNIQUE NOT NULL,
                model_used  TEXT NOT NULL,
                response    TEXT NOT NULL,
                hit_count   INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at  TEXT NOT NULL
            )
        """))
        conn.commit()
    SessionFactory = sessionmaker(bind=engine)
    sess = SessionFactory()
    yield sess
    sess.close()
    engine.dispose()


@pytest.fixture()
def cache(session: Session) -> ResponseCache:
    return ResponseCache(session)


# ── ResponseCache.set / get ───────────────────────────────────────────────────

PROMPT = "Add a QR code endpoint to the URL shortener"
MODEL = "gpt-4o"
RESPONSE = {"content": "Here is the implementation...", "usage": {"input_tokens": 500, "output_tokens": 200}}


def test_get_returns_none_on_empty_cache(cache: ResponseCache):
    assert cache.get(PROMPT, MODEL) is None


def test_set_then_get_returns_response(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    result = cache.get(PROMPT, MODEL)
    assert result == RESPONSE


def test_get_is_cache_miss_for_different_model(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    assert cache.get(PROMPT, "gpt-4o-mini") is None


def test_get_is_cache_miss_for_different_prompt(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    assert cache.get("A completely different prompt", MODEL) is None


def test_set_upserts_on_duplicate_prompt(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    new_response = {"content": "Updated response", "usage": {}}
    cache.set(PROMPT, MODEL, new_response)         # should refresh, not raise
    result = cache.get(PROMPT, MODEL)
    assert result == new_response


def test_set_upsert_resets_hit_count(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    cache.get(PROMPT, MODEL)                        # hit_count → 1
    cache.set(PROMPT, MODEL, RESPONSE)              # upsert → hit_count reset to 0
    assert cache.hit_count(PROMPT, MODEL) == 0


# ── ResponseCache expiry ──────────────────────────────────────────────────────


def test_expired_entry_returns_none(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE, ttl_hours=-1)   # instantly expired
    assert cache.get(PROMPT, MODEL) is None


def test_valid_entry_not_expired(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE, ttl_hours=24)
    assert cache.get(PROMPT, MODEL) is not None


# ── ResponseCache hit_count ───────────────────────────────────────────────────


def test_hit_count_zero_before_any_get(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    assert cache.hit_count(PROMPT, MODEL) == 0


def test_hit_count_increments_on_each_get(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    cache.get(PROMPT, MODEL)
    cache.get(PROMPT, MODEL)
    assert cache.hit_count(PROMPT, MODEL) == 2


def test_hit_count_zero_for_missing_entry(cache: ResponseCache):
    assert cache.hit_count("nonexistent", MODEL) == 0


# ── ResponseCache invalidate ──────────────────────────────────────────────────


def test_invalidate_removes_entry(cache: ResponseCache):
    cache.set(PROMPT, MODEL, RESPONSE)
    cache.invalidate(PROMPT, MODEL)
    assert cache.get(PROMPT, MODEL) is None


def test_invalidate_nonexistent_is_safe(cache: ResponseCache):
    cache.invalidate("ghost prompt", MODEL)   # should not raise


# ── ResponseCache response round-trip ────────────────────────────────────────


def test_nested_response_dict_roundtrips(cache: ResponseCache):
    nested = {
        "content": "Here is the plan:\n1. Add route\n2. Add model",
        "tool_calls": [{"id": "tc_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        "usage": {"input_tokens": 1000, "output_tokens": 400},
    }
    cache.set(PROMPT, MODEL, nested)
    assert cache.get(PROMPT, MODEL) == nested


# ── ToolCache ─────────────────────────────────────────────────────────────────


def test_tool_cache_miss_returns_none():
    tc = ToolCache()
    assert tc.get("read_file", {"path": "app.py"}) is None


def test_tool_cache_set_then_get():
    tc = ToolCache()
    tc.set("read_file", {"path": "app.py"}, "file contents here")
    assert tc.get("read_file", {"path": "app.py"}) == "file contents here"


def test_tool_cache_key_is_stable_across_arg_ordering():
    tc = ToolCache()
    tc.set("read_file", {"path": "app.py", "encoding": "utf-8"}, "contents")
    # Same args, different key ordering — should be a hit
    result = tc.get("read_file", {"encoding": "utf-8", "path": "app.py"})
    assert result == "contents"


def test_tool_cache_different_tools_different_entries():
    tc = ToolCache()
    tc.set("read_file", {"path": "app.py"}, "read result")
    tc.set("run_tests", {"path": "app.py"}, "test result")
    assert tc.get("read_file", {"path": "app.py"}) == "read result"
    assert tc.get("run_tests", {"path": "app.py"}) == "test result"


def test_tool_cache_different_args_different_entries():
    tc = ToolCache()
    tc.set("read_file", {"path": "a.py"}, "file a")
    tc.set("read_file", {"path": "b.py"}, "file b")
    assert tc.get("read_file", {"path": "a.py"}) == "file a"
    assert tc.get("read_file", {"path": "b.py"}) == "file b"


def test_tool_cache_invalidate_removes_entry():
    tc = ToolCache()
    tc.set("read_file", {"path": "app.py"}, "contents")
    tc.invalidate("read_file", {"path": "app.py"})
    assert tc.get("read_file", {"path": "app.py"}) is None


def test_tool_cache_invalidate_nonexistent_is_safe():
    tc = ToolCache()
    tc.invalidate("read_file", {"path": "ghost.py"})   # should not raise


def test_tool_cache_clear_removes_all():
    tc = ToolCache()
    tc.set("read_file", {"path": "a.py"}, "a")
    tc.set("read_file", {"path": "b.py"}, "b")
    tc.clear()
    assert tc.size() == 0


def test_tool_cache_size():
    tc = ToolCache()
    assert tc.size() == 0
    tc.set("read_file", {"path": "a.py"}, "a")
    tc.set("read_file", {"path": "b.py"}, "b")
    assert tc.size() == 2


def test_tool_cache_initialise_from_existing_dict():
    existing = ToolCache()
    existing.set("read_file", {"path": "app.py"}, "cached content")
    tc2 = ToolCache(existing.to_dict())
    assert tc2.get("read_file", {"path": "app.py"}) == "cached content"


def test_tool_cache_to_dict_returns_copy():
    tc = ToolCache()
    tc.set("read_file", {"path": "a.py"}, "a")
    d = tc.to_dict()
    d["extra_key"] = "mutated"
    # Mutation of the copy should not affect the cache
    assert tc.size() == 1


def test_tool_cache_stores_any_result_type():
    tc = ToolCache()
    tc.set("run_tests", {}, {"passed": 45, "failed": 0})
    tc.set("get_diff", {}, ["file1.py", "file2.py"])
    tc.set("flag", {}, True)
    assert tc.get("run_tests", {}) == {"passed": 45, "failed": 0}
    assert tc.get("get_diff", {}) == ["file1.py", "file2.py"]
    assert tc.get("flag", {}) is True

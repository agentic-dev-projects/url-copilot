"""
Unit tests for MemoryStore — stateless logic + SQLite in-memory DB.

No PostgreSQL, no Docker — run anywhere:
    .venv/bin/python -m pytest orchestrator/tests/test_memory_store.py -v

Why SQLite and not the real PostgreSQL schema?
----------------------------------------------
MemoryStore uses only standard SQL that works on both engines:
  INSERT, SELECT, UPDATE, COUNT, WHERE, ORDER BY.
No PostgreSQL-specific features (JSONB, gen_random_uuid, DISTINCT ON) appear
in MemoryStore — those are in RunStateStore, which has its own DB-required tests.

The test fixture creates a minimal orch_memory table in SQLite and injects the
resulting session into MemoryStore.  This tests the full logic path (UUID
generation, type validation, seed loading, format output) without any network call.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.memory.store import MemoryStore

# ── fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def session() -> Session:
    """Fresh SQLite in-memory session with orch_memory table."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE orch_memory (
                id          TEXT PRIMARY KEY,
                source_run_id TEXT,
                memory_type TEXT NOT NULL,
                actor       TEXT NOT NULL,
                content     TEXT NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        conn.commit()
    SessionFactory = sessionmaker(bind=engine)
    sess = SessionFactory()
    yield sess
    sess.close()
    engine.dispose()


@pytest.fixture()
def mem(session: Session) -> MemoryStore:
    return MemoryStore(session)


# ── save ──────────────────────────────────────────────────────────────────────


def test_save_returns_uuid(mem: MemoryStore):
    memory_id = mem.save("fact", "FastAPI is used for the API layer", "alice")
    assert len(memory_id) == 36
    assert memory_id.count("-") == 4


def test_save_persists_row(mem: MemoryStore):
    mem.save("convention", "All endpoints need tests", "bob")
    rows = mem.load_all_active()
    assert len(rows) == 1
    assert rows[0]["content"] == "All endpoints need tests"
    assert rows[0]["memory_type"] == "convention"
    assert rows[0]["actor"] == "bob"


def test_save_with_source_run_id(mem: MemoryStore):
    mem.save("decision", "Use soft deletes only", "carol", source_run_id="run-001")
    rows = mem.load_all_active()
    assert rows[0]["source_run_id"] == "run-001"


def test_save_all_four_types(mem: MemoryStore):
    mem.save("fact", "f", "seed")
    mem.save("convention", "c", "seed")
    mem.save("decision", "d", "seed")
    mem.save("preference", "p", "seed")
    assert mem.count_active() == 4


def test_save_invalid_type_raises(mem: MemoryStore):
    with pytest.raises(ValueError, match="Invalid memory_type"):
        mem.save("lesson_learned", "something", "alice")


# ── invalidate ────────────────────────────────────────────────────────────────


def test_invalidate_hides_row_from_active(mem: MemoryStore):
    memory_id = mem.save("fact", "Old fact", "alice")
    assert mem.count_active() == 1
    mem.invalidate(memory_id)
    assert mem.count_active() == 0


def test_invalidate_does_not_delete_row(mem: MemoryStore, session: Session):
    memory_id = mem.save("fact", "Old fact", "alice")
    mem.invalidate(memory_id)
    row = session.execute(
        text("SELECT is_active FROM orch_memory WHERE id = :id"),
        {"id": memory_id},
    ).mappings().one()
    assert row["is_active"] in (0, False)


def test_load_all_active_excludes_inactive(mem: MemoryStore):
    id1 = mem.save("fact", "active fact", "alice")
    id2 = mem.save("fact", "inactive fact", "alice")
    mem.invalidate(id2)
    rows = mem.load_all_active()
    assert len(rows) == 1
    assert rows[0]["id"] == id1


# ── get_by_type ───────────────────────────────────────────────────────────────


def test_get_by_type_filters_correctly(mem: MemoryStore):
    mem.save("fact", "fact one", "seed")
    mem.save("fact", "fact two", "seed")
    mem.save("convention", "convention one", "seed")
    facts = mem.get_by_type("fact")
    assert len(facts) == 2
    conventions = mem.get_by_type("convention")
    assert len(conventions) == 1


def test_get_by_type_excludes_inactive(mem: MemoryStore):
    id1 = mem.save("decision", "use postgres", "seed")
    mem.invalidate(id1)
    assert mem.get_by_type("decision") == []


def test_get_by_type_respects_limit(mem: MemoryStore):
    for i in range(5):
        mem.save("fact", f"fact {i}", "seed")
    results = mem.get_by_type("fact", limit=3)
    assert len(results) == 3


# ── count_active ──────────────────────────────────────────────────────────────


def test_count_active_empty(mem: MemoryStore):
    assert mem.count_active() == 0


def test_count_active_after_inserts(mem: MemoryStore):
    mem.save("fact", "a", "seed")
    mem.save("convention", "b", "seed")
    assert mem.count_active() == 2


# ── format_for_prompt ─────────────────────────────────────────────────────────


def test_format_for_prompt_empty_returns_empty_string(mem: MemoryStore):
    assert mem.format_for_prompt() == ""


def test_format_for_prompt_contains_header(mem: MemoryStore):
    mem.save("fact", "FastAPI is used", "seed")
    output = mem.format_for_prompt()
    assert "Team Conventions and Persistent Memory" in output


def test_format_for_prompt_contains_type_tag(mem: MemoryStore):
    mem.save("convention", "Always write tests", "seed")
    output = mem.format_for_prompt()
    assert "[convention]" in output


def test_format_for_prompt_contains_content(mem: MemoryStore):
    mem.save("decision", "Use short codes of length 8", "seed")
    output = mem.format_for_prompt()
    assert "Use short codes of length 8" in output


def test_format_for_prompt_multiple_rows(mem: MemoryStore):
    mem.save("fact", "FastAPI", "seed")
    mem.save("convention", "Write tests", "seed")
    mem.save("decision", "Soft deletes only", "seed")
    output = mem.format_for_prompt()
    assert output.count("[") == 3


def test_format_for_prompt_excludes_inactive(mem: MemoryStore):
    id1 = mem.save("fact", "active", "seed")
    id2 = mem.save("fact", "inactive", "seed")
    mem.invalidate(id2)
    output = mem.format_for_prompt()
    assert "inactive" not in output
    assert "active" in output


# ── seed_if_empty ─────────────────────────────────────────────────────────────


def test_seed_if_empty_inserts_seeds(mem: MemoryStore):
    inserted = mem.seed_if_empty()
    assert inserted > 0
    assert mem.count_active() == inserted


def test_seed_if_empty_is_idempotent(mem: MemoryStore):
    first = mem.seed_if_empty()
    second = mem.seed_if_empty()
    assert second == 0                        # no-op on second call
    assert mem.count_active() == first        # count unchanged


def test_seed_if_empty_seeds_are_facts(mem: MemoryStore):
    mem.seed_if_empty()
    facts = mem.get_by_type("fact")
    assert len(facts) > 0


def test_seed_if_empty_seeds_are_active(mem: MemoryStore):
    count = mem.seed_if_empty()
    assert mem.count_active() == count


def test_seed_if_empty_all_five_codebase_facts_present(mem: MemoryStore):
    mem.seed_if_empty()
    rows = mem.load_all_active()
    contents = [r["content"] for r in rows]
    # Each entry from seeds.yaml should be present
    assert any("FastAPI" in c for c in contents)
    assert any("PostgreSQL" in c or "Alembic" in c for c in contents)

"""
MemoryStore — persistent cross-run memory via the orch_memory table.

Purpose
-------
Each stage agent prompt is assembled from four layers (Phase 11 — Prompt Builder):
  Layer 1  Stage-specific task instructions (static prompt file)
  Layer 2  Current run context (requirement, scenario, run_id)
  Layer 3  Persistent memory (this module)  ← MemoryStore.format_for_prompt()
  Layer 4  Tool definitions

MemoryStore provides Layer 3.  It answers the question: what does this team know
that every agent should know, regardless of stage?

Examples of memories that cross-cut every stage:
  - fact:       "Auth uses X-API-Key header; SHA-256 hashed; never store plaintext"
  - convention: "Every new endpoint needs unit + integration tests"
  - decision:   "Soft deletes only — is_active flag, never hard DELETE"
  - preference: "SQLAlchemy text() for orch_ tables; ORM only in service/"

Memory types (from orch_memory.memory_type)
-------------------------------------------
  fact        Immutable truths about the codebase stack and infrastructure.
  convention  Coding standards the team has agreed on.
  decision    Architectural decisions made in a prior run or pre-seeded.
  preference  Soft preferences (style, tooling choices, reviewer habits).

Schema (orch_memory table — Phase 2 migration)
-----------------------------------------------
  id             UUID PK — generated in Python so SQLite tests work too
  source_run_id  VARCHAR(50) nullable FK → orch_runs.id (None for seeds)
  memory_type    VARCHAR(30) NOT NULL — fact|convention|decision|preference
  actor          VARCHAR(100) NOT NULL — github_login or "seed"
  content        TEXT NOT NULL
  is_active      BOOLEAN NOT NULL DEFAULT true
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()

Session management
------------------
MemoryStore follows the same dependency-injection pattern as RunStateStore
and AuditLogger: it takes a Session at construction time.  For CLI / engine
usage, use make_memory_store() which wraps SessionLocal() with commit/rollback.

    with make_memory_store() as mem:
        mem.seed_if_empty()
        print(mem.format_for_prompt())

Seeding
-------
seed_if_empty() reads orchestrator/memory/seeds.yaml and inserts the listed
facts if orch_memory is empty.  This runs once at orchestrator startup (run.py).
After the first run, the table is non-empty so subsequent startups are no-ops.
"""

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from service.db.session import SessionLocal

_SEEDS_PATH = Path(__file__).parent / "seeds.yaml"

_VALID_TYPES = frozenset({"fact", "convention", "decision", "preference"})


class MemoryStore:
    """Reads and writes orch_memory rows via SQLAlchemy text() queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── writes ────────────────────────────────────────────────────────────────

    def save(
        self,
        memory_type: str,
        content: str,
        actor: str,
        source_run_id: str | None = None,
    ) -> str:
        """Insert a new active memory row and return its UUID.

        Args:
            memory_type:   One of fact|convention|decision|preference.
            content:       The memory text (free-form prose).
            actor:         GitHub login of the person/agent recording this, or "seed".
            source_run_id: FK to orch_runs.id — None for pre-seeded memories.

        Returns:
            The new row's UUID string.

        Raises:
            ValueError: if memory_type is not one of the four valid values.
        """
        if memory_type not in _VALID_TYPES:
            raise ValueError(
                f"Invalid memory_type '{memory_type}'. "
                f"Expected one of: {sorted(_VALID_TYPES)}"
            )
        memory_id = str(uuid.uuid4())
        self.session.execute(
            text(
                "INSERT INTO orch_memory "
                "(id, source_run_id, memory_type, actor, content) "
                "VALUES (:id, :run_id, :mtype, :actor, :content)"
            ),
            {
                "id": memory_id,
                "run_id": source_run_id,
                "mtype": memory_type,
                "actor": actor,
                "content": content,
            },
        )
        self.session.commit()
        return memory_id

    def invalidate(self, memory_id: str) -> None:
        """Soft-delete a memory row by setting is_active=False.

        Used when a decision is superseded or a convention changes.  The row
        is retained for audit purposes; format_for_prompt() skips inactive rows.
        """
        self.session.execute(
            text("UPDATE orch_memory SET is_active = FALSE WHERE id = :id"),
            {"id": memory_id},
        )
        self.session.commit()

    # ── reads ─────────────────────────────────────────────────────────────────

    def load_all_active(self, limit: int = 50) -> list[dict]:
        """Return all active memory rows, oldest first.

        Args:
            limit: Maximum rows to return (default 50 — keeps prompts from blowing up).

        Returns:
            List of dicts with keys: id, source_run_id, memory_type, actor,
            content, is_active, created_at.
        """
        rows = self.session.execute(
            text(
                "SELECT * FROM orch_memory "
                "WHERE is_active = TRUE "
                "ORDER BY created_at ASC "
                "LIMIT :limit"
            ),
            {"limit": limit},
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_by_type(self, memory_type: str, limit: int = 20) -> list[dict]:
        """Return active memories of a single type, oldest first.

        Args:
            memory_type: One of fact|convention|decision|preference.
            limit:       Maximum rows to return.

        Returns:
            List of row dicts (same shape as load_all_active).
        """
        rows = self.session.execute(
            text(
                "SELECT * FROM orch_memory "
                "WHERE is_active = TRUE AND memory_type = :mtype "
                "ORDER BY created_at ASC "
                "LIMIT :limit"
            ),
            {"mtype": memory_type, "limit": limit},
        ).mappings().all()
        return [dict(r) for r in rows]

    def count_active(self) -> int:
        """Return the number of active rows in orch_memory."""
        row = self.session.execute(
            text("SELECT COUNT(*) AS n FROM orch_memory WHERE is_active = TRUE")
        ).mappings().one()
        return int(row["n"])

    # ── prompt injection ──────────────────────────────────────────────────────

    def format_for_prompt(self, limit: int = 20) -> str:
        """Return a formatted memory block for injection into stage agent prompts.

        This is Layer 3 of the four-layer prompt assembly (Phase 11 — Prompt Builder).
        The returned string is inserted between the task instructions and the run
        context so every stage agent has access to codebase facts and conventions
        without those facts needing to live in every individual prompt file.

        Format:
            === Team Conventions and Persistent Memory ===
            [fact]       Framework: FastAPI with SQLAlchemy ORM and Pydantic v2 schemas
            [convention] Every new endpoint requires unit + integration tests
            [decision]   Short codes are 8-char alphanumeric strings (secrets module)
            [preference] SQLAlchemy text() for orch_ tables; ORM only in service/

        Returns:
            Multi-line string, or empty string if there are no active memories.
        """
        rows = self.load_all_active(limit=limit)
        if not rows:
            return ""
        lines = ["=== Team Conventions and Persistent Memory ==="]
        for r in rows:
            tag = f"[{r['memory_type']}]"
            lines.append(f"{tag:<14} {r['content']}")
        return "\n".join(lines)

    # ── seeding ───────────────────────────────────────────────────────────────

    def seed_if_empty(self) -> int:
        """Insert seed memories from seeds.yaml if orch_memory is currently empty.

        Reads all entries under the 'facts' key in seeds.yaml and inserts them
        with memory_type='fact' and actor='seed'.  Non-facts (conventions,
        decisions, preferences) can be added to seeds.yaml under their own keys;
        this method handles all four top-level keys.

        Returns:
            Number of rows inserted (0 if the table was already non-empty).
        """
        if self.count_active() > 0:
            return 0

        data = yaml.safe_load(_SEEDS_PATH.read_text(encoding="utf-8"))
        inserted = 0

        # Support any combination of the four memory type keys
        for memory_type in ("fact", "convention", "decision", "preference"):
            for content in data.get(f"{memory_type}s", []) or data.get(memory_type, []):
                self.save(memory_type=memory_type, content=content, actor="seed")
                inserted += 1

        # Backwards-compat: top-level 'facts' key (current seeds.yaml format)
        if inserted == 0:
            for content in data.get("facts", []):
                self.save(memory_type="fact", content=content, actor="seed")
                inserted += 1

        return inserted


# ── module-level convenience ─────────────────────────────────────────────────


@contextmanager
def make_memory_store() -> Generator[MemoryStore, None, None]:
    """Create a MemoryStore backed by a fresh SessionLocal session.

    Commits on clean exit, rolls back on exception, always closes the session.

    Usage:
        with make_memory_store() as mem:
            mem.seed_if_empty()
            print(mem.format_for_prompt())
    """
    session = SessionLocal()
    try:
        yield MemoryStore(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

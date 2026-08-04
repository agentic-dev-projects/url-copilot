"""
Live end-to-end tests for all three orchestrator scenarios.

These tests make REAL OpenAI API calls and write to the PostgreSQL database.
They are guarded behind RUN_E2E=1 so they never run in normal CI.

Prerequisites
-------------
1. pip install langgraph openai
2. Set OPENAI_API_KEY in .env or environment
3. Ensure PostgreSQL is running and orch_* tables exist:
       alembic upgrade head
4. Run with:
       RUN_E2E=1 .venv/bin/python -m pytest orchestrator/tests/test_e2e_live.py -v -s

What is REAL vs MOCKED
-----------------------
REAL (uses live OpenAI API):
  RequirementClassifier   — gpt-4o-mini classifies requirement into scenario type
  ClarificationLoop       — gpt-4o-mini generates questions + resolves requirement
  StageAgent              — gpt-4o executes each pipeline stage with tool calls
  PromptBuilder           — reads actual service/ codebase files for context
  AIGateway               — full 11-step pipeline (auth, rate limit, guardrails, cost)
  MemoryStore             — real PostgreSQL reads/writes (seeds on first run)
  ResponseCache           — real PostgreSQL reads/writes (caches stage responses)
  AuditLogger             — real PostgreSQL writes to orch_audit_events
  RunStateStore           — real PostgreSQL writes to orch_runs + orch_stage_results
  MetricsTracker          — real PostgreSQL reads from orch_metrics

MOCKED (safe for automated tests):
  interrupt()             — auto-approves all human gates (no CLI interaction)
  write_file              — returns success without modifying service/ files
  run_tests               — returns 45 passed without running pytest
  run_linter              — returns no violations
  create_branch           — returns (201, branch_name) without calling GitHub
  create_pr               — returns (201, PR_URL) without calling GitHub
  ClarificationLoop.ask_fn — returns a fixed answer (no stdin needed)

Cost estimate: ~$0.10–0.40 per full pipeline run (14 stages × gpt-4o).
"""

import os
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv
load_dotenv()  # load .env before openai.OpenAI() is constructed inside AIGateway

import pytest

# ── Skip guard ────────────────────────────────────────────────────────────────

_E2E_ENABLED = os.getenv("RUN_E2E", "0") == "1"
_SKIP_MSG = (
    "Live E2E tests disabled. Set RUN_E2E=1 and ensure OPENAI_API_KEY is set.\n"
    "  RUN_E2E=1 .venv/bin/python -m pytest orchestrator/tests/test_e2e_live.py -v -s"
)

pytestmark = pytest.mark.skipif(not _E2E_ENABLED, reason=_SKIP_MSG)

# ── Optional dependency check ─────────────────────────────────────────────────

try:
    import langgraph  # noqa: F401
    _LANGGRAPH = True
except ImportError:
    _LANGGRAPH = False

try:
    import openai  # noqa: F401
    _OPENAI = True
except ImportError:
    _OPENAI = False

if _E2E_ENABLED:
    if not _LANGGRAPH:
        pytest.exit("langgraph not installed. Run: pip install langgraph", returncode=1)
    if not _OPENAI:
        pytest.exit("openai not installed. Run: pip install openai", returncode=1)
    if not os.getenv("OPENAI_API_KEY"):
        pytest.exit("OPENAI_API_KEY is not set in environment.", returncode=1)


# ── Helpers ───────────────────────────────────────────────────────────────────

_TEST_TOKEN = "dave_admin_token"   # ADMIN — unlimited budget, all permissions


def _e2e_run_id() -> str:
    """Generate a unique run_id prefixed so cleanup can target it."""
    return f"e2e-{uuid.uuid4().hex[:8]}"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def session():
    """Real PostgreSQL session.  Rolls back after the test to leave DB clean."""
    from service.db.session import SessionLocal
    sess = SessionLocal()
    yield sess
    sess.close()


@pytest.fixture(autouse=True)
def auto_approve_gates():
    """Patch interrupt() so all human gate nodes auto-approve without CLI input.

    interrupt() in nodes.py receives the gate payload dict and normally pauses
    the LangGraph graph.  Here we return a pre-canned approval immediately so
    the pipeline runs to completion without any human interaction.
    """
    approval = {"approved": True, "approver": "bob", "comment": "auto-approved for E2E"}
    with patch("orchestrator.scenarios.nodes.interrupt", return_value=approval):
        yield


@pytest.fixture(autouse=True)
def safe_tools():
    """Patch tools that have write side-effects with safe no-op fakes.

    read_file, list_directory, and search_codebase are left real so the LLM
    receives genuine codebase context — this produces better, more realistic
    stage artifacts.  Only tools that mutate state are swapped out.
    """
    from orchestrator.tools import registry

    fake_write   = lambda path, content: {"success": True, "path": path, "bytes_written": len(content)}
    fake_tests   = lambda path=".": {"passed": 45, "failed": 0, "errors": 0, "success": True, "output": "45 passed in 2.31s"}
    fake_linter  = lambda path=".": {"passed": True, "violations": [], "count": 0}
    fake_branch  = lambda name, base="main": (201, name)
    fake_pr      = lambda title, body, head, base="main": (201, "https://github.com/agentic-dev-projects/url-copilot/pull/1")
    fake_poll    = lambda pr_number: "open"

    originals = {k: registry.TOOLS[k] for k in ("write_file", "run_tests", "run_linter",
                                                   "create_branch", "create_pr", "poll_pr_status")}
    registry.TOOLS.update({
        "write_file":     fake_write,
        "run_tests":      fake_tests,
        "run_linter":     fake_linter,
        "create_branch":  fake_branch,
        "create_pr":      fake_pr,
        "poll_pr_status": fake_poll,
    })
    yield
    registry.TOOLS.update(originals)


@pytest.fixture()
def seed_run(session):
    """Pre-insert an orch_runs row so CostTracker's FK constraint is satisfied.

    RequirementClassifier and ClarificationLoop use a fallback run_id
    ("classify", "clarify-questions") when no run_id is provided.
    orch_metrics.run_id has a FK to orch_runs.id, so a parent row must exist
    before any orch_metrics INSERT happens inside AIGateway.call().

    Yields the run_id so tests can pass it to classify() / loop.run().
    Deletes the row (and any orch_metrics children) after the test.
    """
    from sqlalchemy import text

    run_id = _e2e_run_id()
    session.execute(
        text(
            "INSERT INTO orch_runs (id, requirement, scenario_type, status, triggered_by) "
            "VALUES (:id, :req, 'greenfield', 'running', 'dave')"
        ),
        {"id": run_id, "req": "e2e classifier/clarification test"},
    )
    session.commit()
    yield run_id
    # cleanup
    for table in ("orch_metrics", "orch_audit_events", "orch_stage_results", "orch_runs"):
        try:
            session.execute(text(f"DELETE FROM {table} WHERE run_id = :rid"), {"rid": run_id})  # noqa: S608
        except Exception:
            pass
    try:
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def db_cleanup(session):
    """Collect run_ids during a test and delete all related rows after."""
    run_ids: list[str] = []
    yield run_ids

    if not run_ids:
        return

    from sqlalchemy import text
    for run_id in run_ids:
        for table in ("orch_stage_results", "orch_audit_events", "orch_metrics",
                      "orch_memory", "orch_runs"):
            try:
                session.execute(
                    text(f"DELETE FROM {table} WHERE run_id = :rid"),  # noqa: S608
                    {"rid": run_id},
                )
            except Exception:
                pass
    try:
        session.commit()
    except Exception:
        session.rollback()


def _build_gateway_and_planner(gateway=None):
    """Build the AIGateway, Planner stack.  Returns (gateway, planner)."""
    from orchestrator.gateway.gateway import AIGateway
    from orchestrator.planner.classifier import RequirementClassifier
    from orchestrator.planner.clarification import ClarificationLoop
    from orchestrator.planner.planner import Planner

    gw = gateway or AIGateway()
    classifier    = RequirementClassifier(gw)
    clarification = ClarificationLoop(gw)
    planner       = Planner(classifier, clarification, run_store=None)
    return gw, planner


def _build_engine(gateway, session, run_store):
    """Wire all 14 nodes and return (nodes, memory_store, scenario classes)."""
    from langgraph.checkpoint.memory import MemorySaver

    from orchestrator.core.engine import OrchestrationEngine
    from orchestrator.gateway.gateway import AIGateway
    from orchestrator.memory.store import MemoryStore
    from orchestrator.scenarios.ambiguous import AmbiguousScenario
    from orchestrator.scenarios.brownfield import BrownfieldScenario
    from orchestrator.scenarios.greenfield import GreenFieldScenario
    from orchestrator.scenarios.nodes import make_gate_node, make_stage_node
    from orchestrator.tools.registry import ToolRegistry
    from service.db.session import SessionLocal

    _GATE_PERMISSIONS = {
        "architecture_gate": "approve_architecture",
        "schema_gate":       "approve_schema_change",
        "tests_gate":        "approve_architecture",
        "pr_gate":           "approve_release",
        "release_gate":      "approve_release",
    }

    memory_store = MemoryStore(session)
    memory_store.seed_if_empty()

    registry = ToolRegistry()

    all_names   = GreenFieldScenario.node_names()
    gate_names  = {n for n in all_names if n.endswith("_gate")}
    stage_names = [n for n in all_names if n not in gate_names]

    nodes: dict[str, Any] = {}
    for name in stage_names:
        nodes[name] = make_stage_node(name, gateway, registry, SessionLocal)
    for name in gate_names:
        nodes[name] = make_gate_node(name, _GATE_PERMISSIONS[name], SessionLocal)

    return nodes, memory_store, GreenFieldScenario, BrownfieldScenario, AmbiguousScenario


# ── SCENARIO A: Classifier smoke tests (cheap — gpt-4o-mini only) ─────────────


class TestClassifierScenarios:
    """Verify RequirementClassifier correctly buckets all three scenario types."""

    def test_greenfield_requirement(self, session, seed_run):
        """A clear new-feature request should be classified as greenfield."""
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.planner.classifier import RequirementClassifier

        gw     = AIGateway()
        result = RequirementClassifier(gw).classify(
            "Add a GET /api/v1/urls/{short_code}/qr endpoint that returns "
            "a PNG QR code image for the given short URL.",
            token=_TEST_TOKEN,
            run_id=seed_run,
        )
        print(f"\n[greenfield] scenario={result.scenario_type} confidence={result.confidence:.2f}")
        print(f"  reasoning: {result.reasoning[:120]}")

        assert result.scenario_type == "greenfield"
        assert result.confidence >= 0.6

    def test_brownfield_requirement(self, session, seed_run):
        """A request to modify existing behavior should be classified as brownfield."""
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.planner.classifier import RequirementClassifier

        gw     = AIGateway()
        result = RequirementClassifier(gw).classify(
            "Change the redirect endpoint to return a 302 instead of 301 "
            "and add an X-Redirected-By header with the value 'url-copilot'.",
            token=_TEST_TOKEN,
            run_id=seed_run,
        )
        print(f"\n[brownfield] scenario={result.scenario_type} confidence={result.confidence:.2f}")
        print(f"  reasoning: {result.reasoning[:120]}")

        assert result.scenario_type == "brownfield"
        assert result.confidence >= 0.6

    def test_ambiguous_requirement(self, session, seed_run):
        """A vague request should be classified as ambiguous with a clarifying question."""
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.planner.classifier import RequirementClassifier

        gw     = AIGateway()
        result = RequirementClassifier(gw).classify(
            "Improve the system.",
            token=_TEST_TOKEN,
            run_id=seed_run,
        )
        print(f"\n[ambiguous] scenario={result.scenario_type} confidence={result.confidence:.2f}")
        print(f"  clarification: {result.clarification_needed}")

        assert result.scenario_type == "ambiguous"
        assert result.clarification_needed is not None


# ── SCENARIO B: Clarification loop (cheap — 2 × gpt-4o-mini) ─────────────────


class TestClarificationLoop:
    """Verify the two-LLM-call clarification loop resolves an ambiguous requirement."""

    def test_resolves_ambiguous_to_scoped_requirement(self, session, seed_run):
        """ClarificationLoop should produce a resolved_requirement with assumptions."""
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.planner.clarification import ClarificationLoop

        gw   = AIGateway()
        loop = ClarificationLoop(
            gw,
            ask_fn=lambda q: "For all authenticated users using JWT tokens, "
                             "targeting the GET /api/v1/urls endpoint.",
        )
        result = loop.run(
            requirement="Make the URLs faster.",
            token=_TEST_TOKEN,
            run_id=seed_run,
        )

        print(f"\n[clarification] resolved={result.resolved_requirement[:120]}")
        print(f"  assumptions: {result.assumptions}")

        assert len(result.resolved_requirement) > 20
        assert isinstance(result.assumptions, list)


# ── SCENARIO C: Full greenfield pipeline (expensive — 14 × gpt-4o) ───────────


class TestGreenfieldFullPipeline:
    """
    Run the complete 14-node greenfield pipeline end-to-end.

    What this verifies
    ------------------
    - RequirementClassifier classifies the requirement as greenfield
    - Planner creates OrchestratorState with a valid run_id
    - All 14 LangGraph nodes execute (stage + gate)
    - Fan-out (architecture_gate → implementation_plan + test_plan) resolves
    - Fan-in ([implementation_plan, test_plan] → implementation) resolves
    - schema_gate is SKIPPED (no schema change in this requirement)
    - All 4 interrupt() gates auto-approve and pipeline continues
    - All stage artifacts are populated in the final state
    - orch_runs + orch_stage_results rows are written to PostgreSQL
    - orch_metrics rows are written for each LLM call
    - MetricsTracker.summarize() returns non-zero cost and tokens

    Estimated run time: 3–8 minutes (14 real OpenAI calls)
    Estimated cost:     $0.10–0.40
    """

    def test_full_pipeline_to_completion(self, session, db_cleanup):
        from langgraph.checkpoint.memory import MemorySaver

        from orchestrator.core.engine import OrchestrationEngine
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.gateway.auth import TokenAuthenticator
        from orchestrator.metrics.tracker import MetricsTracker
        from orchestrator.planner.classifier import RequirementClassifier
        from orchestrator.planner.clarification import ClarificationLoop
        from orchestrator.planner.planner import Planner
        from orchestrator.scenarios.greenfield import GreenFieldScenario
        from orchestrator.state.store import RunStateStore

        auth      = TokenAuthenticator()
        user      = auth.resolve(_TEST_TOKEN)
        gateway   = AIGateway()
        run_store = RunStateStore(session)

        # ── Plan ──────────────────────────────────────────────────────────────
        classifier    = RequirementClassifier(gateway)
        clarification = ClarificationLoop(gateway)
        planner       = Planner(classifier, clarification, run_store)

        state = planner.plan(
            requirement=(
                "Add a GET /api/v1/urls/{short_code}/qr endpoint that generates "
                "and returns a PNG QR code image for the destination URL of the "
                "given short code.  No authentication required.  Use the qrcode "
                "Python library."
            ),
            triggered_by=user.github_login,
            token=_TEST_TOKEN,
        )
        run_id = state["run_id"]
        db_cleanup.append(run_id)

        print(f"\n{'='*60}")
        print(f"  Run ID:   {run_id}")
        print(f"  Scenario: {state['scenario_type']}")
        print(f"{'='*60}")

        assert state["scenario_type"] == "greenfield", (
            f"Expected greenfield, got {state['scenario_type']}"
        )

        # ── Build pipeline ────────────────────────────────────────────────────
        nodes, memory_store, GreenFieldScenario, _, _ = _build_engine(gateway, session, run_store)
        engine = OrchestrationEngine(
            scenario=GreenFieldScenario(nodes),
            checkpointer=MemorySaver(),
        )

        # ── Execute ───────────────────────────────────────────────────────────
        print("Running pipeline (this will take several minutes)...")
        final_state = engine.run(state)

        # ── Verify graph completed ────────────────────────────────────────────
        snapshot = engine.get_state(run_id)
        assert not snapshot.next, (
            f"Graph is still paused — expected completion. Pending: {snapshot.next}"
        )

        # ── Verify all 14 nodes produced artifacts ────────────────────────────
        artifacts = final_state.get("stage_artifacts", {})
        all_nodes = GreenFieldScenario.node_names()
        # schema_gate is conditional — skip it if schema_change_detected was False
        expected = [n for n in all_nodes if n != "schema_gate"]

        missing = [n for n in expected if n not in artifacts]
        assert not missing, f"Missing artifacts for nodes: {missing}"

        print("\nArtifacts produced:")
        for name in all_nodes:
            status = "✓" if name in artifacts else "○ (skipped — no schema change)"
            print(f"  {status}  {name}")

        # ── Verify schema_gate was skipped ────────────────────────────────────
        assert "schema_gate" not in artifacts, (
            "schema_gate should not have run for a non-DB-migration requirement"
        )

        # ── Verify DB records ─────────────────────────────────────────────────
        run_row = run_store.get_run(run_id)
        assert run_row["scenario_type"] == "greenfield"
        assert run_row["triggered_by"] == user.github_login

        stage_results = run_store.get_all_stage_results(run_id)
        completed_stages = [r for r in stage_results if r["status"] == "completed"]
        assert len(completed_stages) >= 9, (
            f"Expected at least 9 completed stages, got {len(completed_stages)}"
        )

        # ── Verify metrics ────────────────────────────────────────────────────
        tracker = MetricsTracker(session)
        summary = tracker.summarize(run_id)

        print(f"\n{'='*60}")
        print(f"  METRICS SUMMARY — {run_id}")
        print(f"{'='*60}")
        print(f"  Total cost:     ${summary['total_cost_usd']:.4f}")
        print(f"  Total tokens:   {summary['total_tokens']:,}")
        print(f"  Cache hit rate: {summary['cache_hit_rate']*100:.1f}%")
        print(f"  Avg latency:    {summary['avg_llm_latency_ms']:.0f} ms")
        print(f"  Stages done:    {summary['stages_completed']}")
        print(f"{'='*60}")

        assert summary["total_tokens"] > 0, "Expected non-zero token usage in orch_metrics"
        assert summary["total_cost_usd"] > 0, "Expected non-zero cost in orch_metrics"
        assert summary["stages_completed"] >= 9

        # ── Mark run completed ────────────────────────────────────────────────
        run_store.update_run_completed(run_id, feedback_score=4,
                                       feedback_comment="E2E test passed")

        print("\nE2E test PASSED ✓")


# ── SCENARIO D: Brownfield full pipeline ─────────────────────────────────────


class TestBrownfieldFullPipeline:
    """
    Run the complete pipeline for a brownfield (modify-existing) requirement.

    The pipeline topology is identical to greenfield (BrownfieldScenario inherits
    GreenFieldScenario).  This test verifies the classifier correctly routes the
    run to BrownfieldScenario and all stages complete.

    Estimated run time: 3–8 minutes
    Estimated cost:     $0.10–0.40
    """

    def test_brownfield_pipeline_to_completion(self, session, db_cleanup):
        from langgraph.checkpoint.memory import MemorySaver

        from orchestrator.core.engine import OrchestrationEngine
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.gateway.auth import TokenAuthenticator
        from orchestrator.metrics.tracker import MetricsTracker
        from orchestrator.planner.classifier import RequirementClassifier
        from orchestrator.planner.clarification import ClarificationLoop
        from orchestrator.planner.planner import Planner
        from orchestrator.scenarios.brownfield import BrownfieldScenario
        from orchestrator.scenarios.greenfield import GreenFieldScenario
        from orchestrator.state.store import RunStateStore

        auth      = TokenAuthenticator()
        user      = auth.resolve(_TEST_TOKEN)
        gateway   = AIGateway()
        run_store = RunStateStore(session)

        classifier    = RequirementClassifier(gateway)
        clarification = ClarificationLoop(gateway)
        planner       = Planner(classifier, clarification, run_store)

        state = planner.plan(
            requirement=(
                "Change the redirect endpoint GET /api/v1/{short_code} to return "
                "HTTP 302 instead of 301, and add an X-Redirect-Reason header "
                "with value 'url-copilot'."
            ),
            triggered_by=user.github_login,
            token=_TEST_TOKEN,
        )
        run_id = state["run_id"]
        db_cleanup.append(run_id)

        print(f"\n  Run ID:   {run_id}")
        print(f"  Scenario: {state['scenario_type']}")

        assert state["scenario_type"] == "brownfield", (
            f"Expected brownfield, got {state['scenario_type']}"
        )

        nodes, _, _, BrownfieldScenario, _ = _build_engine(gateway, session, run_store)
        engine = OrchestrationEngine(
            scenario=BrownfieldScenario(nodes),
            checkpointer=MemorySaver(),
        )

        final_state = engine.run(state)
        snapshot    = engine.get_state(run_id)
        assert not snapshot.next, f"Graph still paused: {snapshot.next}"

        artifacts = final_state.get("stage_artifacts", {})
        expected  = [n for n in GreenFieldScenario.node_names() if n != "schema_gate"]
        missing   = [n for n in expected if n not in artifacts]
        assert not missing, f"Missing artifacts: {missing}"

        tracker = MetricsTracker(session)
        summary = tracker.summarize(run_id)
        assert summary["total_tokens"] > 0

        run_store.update_run_completed(run_id)
        print("  Brownfield E2E test PASSED ✓")


# ── SCENARIO E: Ambiguous requirement with clarification ──────────────────────


class TestAmbiguousFullPipeline:
    """
    Run the complete pipeline for an ambiguous requirement.

    The clarification loop runs before the pipeline starts, producing a
    resolved_requirement and assumptions list that are injected into the state.
    The pipeline then runs on the resolved requirement — same 14-node topology.

    ask_fn is injected to avoid stdin interaction in the test.

    Estimated run time: 3–8 minutes (+ 2 extra LLM calls for clarification)
    Estimated cost:     $0.10–0.40
    """

    def test_ambiguous_pipeline_to_completion(self, session, db_cleanup):
        from langgraph.checkpoint.memory import MemorySaver

        from orchestrator.core.engine import OrchestrationEngine
        from orchestrator.gateway.gateway import AIGateway
        from orchestrator.gateway.auth import TokenAuthenticator
        from orchestrator.metrics.tracker import MetricsTracker
        from orchestrator.planner.clarification import ClarificationLoop
        from orchestrator.planner.classifier import RequirementClassifier
        from orchestrator.planner.planner import Planner
        from orchestrator.scenarios.ambiguous import AmbiguousScenario
        from orchestrator.scenarios.greenfield import GreenFieldScenario
        from orchestrator.state.store import RunStateStore

        auth      = TokenAuthenticator()
        user      = auth.resolve(_TEST_TOKEN)
        gateway   = AIGateway()
        run_store = RunStateStore(session)

        classifier    = RequirementClassifier(gateway)
        clarification = ClarificationLoop(
            gateway,
            ask_fn=lambda q: (
                "The feature should be accessible to all users without authentication. "
                "It should return JSON, not HTML. Target the existing short URL model."
            ),
        )
        planner = Planner(classifier, clarification, run_store)

        state = planner.plan(
            requirement="Add analytics.",
            triggered_by=user.github_login,
            token=_TEST_TOKEN,
        )
        run_id = state["run_id"]
        db_cleanup.append(run_id)

        print(f"\n  Run ID:   {run_id}")
        print(f"  Scenario: {state['scenario_type']}")
        print(f"  Resolved: {state.get('resolved_requirement', '')[:100]}")
        print(f"  Assumptions: {state.get('assumptions', [])}")

        # Classifier may return ambiguous or greenfield after clarification.
        # Either is valid — the key check is that resolved_requirement is populated.
        assert state.get("resolved_requirement", "") != state["requirement"], (
            "resolved_requirement should differ from the raw ambiguous requirement"
        )

        ScenarioCls = {
            "ambiguous":  AmbiguousScenario,
            "greenfield": GreenFieldScenario,
        }.get(state["scenario_type"], AmbiguousScenario)

        nodes, _, _, _, _ = _build_engine(gateway, session, run_store)
        engine = OrchestrationEngine(
            scenario=ScenarioCls(nodes),
            checkpointer=MemorySaver(),
        )

        final_state = engine.run(state)
        snapshot    = engine.get_state(run_id)
        assert not snapshot.next, f"Graph still paused: {snapshot.next}"

        artifacts = final_state.get("stage_artifacts", {})
        expected  = [n for n in GreenFieldScenario.node_names() if n != "schema_gate"]
        missing   = [n for n in expected if n not in artifacts]
        assert not missing, f"Missing artifacts: {missing}"

        tracker = MetricsTracker(session)
        summary = tracker.summarize(run_id)
        assert summary["total_tokens"] > 0

        run_store.update_run_completed(run_id)
        print("  Ambiguous E2E test PASSED ✓")

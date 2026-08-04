"""
orchestrator.run — CLI entry point for the AI SDLC Orchestrator.

Commands
--------
run      Trigger a new orchestration run for a natural-language requirement.
         Handles all human gate approvals interactively within the same process.

approve  Approve a human gate on a paused run from a DIFFERENT terminal.
         Requires PostgreSQL (DATABASE_URL) for cross-process checkpointing.

Usage
-----
    # Start a run (as a developer)
    python -m orchestrator.run run "Add QR code endpoint GET /api/v1/urls/{id}/qr" \\
        --token alice_dev_token

    # Approve an architecture gate interactively (prompted during 'run')
    # OR approve from a different terminal (requires PostgreSQL):
    python -m orchestrator.run approve \\
        --run-id orch-a3f8c12e \\
        --gate architecture \\
        --token bob_tl_token

Dependency handling
-------------------
langgraph and openai are optional at import time.  Missing packages produce a
clear error message rather than a traceback.  All heavy imports live inside
handle_run / handle_approve so the `--help` flag always works regardless of
which packages are installed.

Gate loop
---------
When the pipeline reaches a human gate, LangGraph's interrupt() pauses execution
and engine.run() returns the current state.  handle_run detects the pause via
engine.get_state().next and prompts the approver interactively before calling
engine.resume().  This loop repeats until the graph reaches END.

PostgresSaver
-------------
In-process runs use MemorySaver (no PostgreSQL needed).  The separate 'approve'
command uses PostgresSaver so the approver can resume a paused run from a
different terminal.  Set DATABASE_URL=postgresql+psycopg://... to enable it.
"""

import argparse
import os
import sys
from typing import Any

# ── Optional langgraph check (fast, done at module load for --help) ───────────
try:
    from langgraph.checkpoint.memory import MemorySaver as _MemorySaver
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False

# ── Gate → required permission mapping ───────────────────────────────────────
_GATE_PERMISSIONS: dict[str, str] = {
    "architecture_gate": "approve_architecture",
    "schema_gate":       "approve_schema_change",
    "tests_gate":        "approve_architecture",  # tech lead also reviews test output
    "pr_gate":           "approve_release",       # release manager approves PR creation
    "release_gate":      "approve_release",
}

# CLI --gate choices → gate node name (for the approve sub-command)
_GATE_CHOICES: dict[str, str] = {
    "architecture":  "architecture_gate",
    "schema_change": "schema_gate",
    "tests":         "tests_gate",
    "pr":            "pr_gate",
    "release":       "release_gate",
}


# ── Argument parser ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.run",
        description="AI SDLC Orchestrator CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # ── run ───────────────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Start a new orchestration run")
    run_p.add_argument("requirement", type=str,
                       help="Natural-language feature requirement")
    run_p.add_argument("--token", required=True,
                       help="Auth token (see orchestrator/config/users.yaml)")

    # ── approve ───────────────────────────────────────────────────────────────
    approve_p = sub.add_parser("approve", help="Approve a paused human gate")
    approve_p.add_argument("--run-id", required=True,
                           help="Run ID from a previous 'run' command")
    approve_p.add_argument("--gate", required=True,
                           choices=list(_GATE_CHOICES.keys()),
                           help="Which gate to approve")
    approve_p.add_argument("--token", required=True,
                           help="Approver auth token (must differ from run requester)")

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Dispatch CLI subcommands: run or approve."""
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        handle_run(args.requirement, args.token)
    elif args.command == "approve":
        handle_approve(args.run_id, args.gate, args.token)


# ── handle_run ────────────────────────────────────────────────────────────────


def handle_run(requirement: str, token: str) -> None:
    """Classify, plan, execute the full pipeline, and print a metrics summary."""
    if not _LANGGRAPH_AVAILABLE:
        print("ERROR: langgraph not installed.")
        print("  Run: pip install langgraph")
        sys.exit(1)

    # ── Lazy imports (keep top-level clean; avoid openai import at --help time) ─
    from langgraph.checkpoint.memory import MemorySaver

    from orchestrator.agents.stage_agent import StageAgent
    from orchestrator.cache.response_cache import ResponseCache
    from orchestrator.core.engine import OrchestrationEngine
    from orchestrator.gateway.auth import TokenAuthenticator
    from orchestrator.gateway.gateway import AIGateway
    from orchestrator.governance.audit import AuditLogger
    from orchestrator.governance.checkpoint import RBACCheckpoint
    from orchestrator.memory.store import MemoryStore
    from orchestrator.metrics.tracker import MetricsTracker
    from orchestrator.planner.clarification import ClarificationLoop
    from orchestrator.planner.classifier import RequirementClassifier
    from orchestrator.planner.planner import Planner
    from orchestrator.scenarios.ambiguous import AmbiguousScenario
    from orchestrator.scenarios.brownfield import BrownfieldScenario
    from orchestrator.scenarios.greenfield import GreenFieldScenario
    from orchestrator.scenarios.nodes import make_gate_node, make_stage_node
    from orchestrator.state.store import RunStateStore
    from orchestrator.tools.registry import ToolRegistry
    from service.db.session import SessionLocal

    session = SessionLocal()
    try:
        # ── Shared infrastructure ─────────────────────────────────────────────
        run_store    = RunStateStore(session)
        audit        = AuditLogger(session)
        memory_store = MemoryStore(session)
        cache        = ResponseCache(session)
        registry     = ToolRegistry()
        gateway      = AIGateway()
        auth         = TokenAuthenticator()
        rbac         = RBACCheckpoint(auth)

        # Seed persistent memory with URL-shortener conventions (no-op if already seeded)
        memory_store.seed_if_empty()

        # ── Authenticate caller ───────────────────────────────────────────────
        user = auth.resolve(token)
        print(f"\nAuthenticated as: {user.github_login} ({user.role})")

        # ── Plan ──────────────────────────────────────────────────────────────
        classifier   = RequirementClassifier(gateway)
        clarification = ClarificationLoop(gateway)
        planner      = Planner(classifier, clarification, run_store)

        print("Classifying requirement...")
        state = planner.plan(
            requirement=requirement,
            triggered_by=user.github_login,
            token=token,
        )
        run_id = state["run_id"]
        print(f"Run ID:   {run_id}")
        print(f"Scenario: {state['scenario_type']}")
        if state.get("assumptions"):
            print("Assumptions made during clarification:")
            for a in state["assumptions"]:
                print(f"  • {a}")

        # ── Build nodes ───────────────────────────────────────────────────────
        agent = StageAgent(gateway, registry, cache)

        all_node_names = GreenFieldScenario.node_names()
        gate_names  = {n for n in all_node_names if n.endswith("_gate")}
        stage_names = [n for n in all_node_names if n not in gate_names]

        nodes: dict[str, Any] = {}
        for name in stage_names:
            nodes[name] = make_stage_node(name, agent, memory_store, run_store, audit)
        for name in gate_names:
            nodes[name] = make_gate_node(
                gate_name=name,
                required_permission=_GATE_PERMISSIONS[name],
                audit=audit,
                hybrid_gate=None,   # interactive approval handled below in gate loop
            )

        # ── Select scenario ───────────────────────────────────────────────────
        scenario_cls = {
            "greenfield": GreenFieldScenario,
            "brownfield": BrownfieldScenario,
            "ambiguous":  AmbiguousScenario,
        }.get(state["scenario_type"], GreenFieldScenario)

        engine = OrchestrationEngine(
            scenario=scenario_cls(nodes),
            checkpointer=MemorySaver(),
        )

        # ── Execute with interactive gate loop ────────────────────────────────
        print(f"\nStarting pipeline for run {run_id}...")
        print("-" * 60)

        _run_gate_loop(engine, state, user.github_login, rbac, run_store)

        # ── End-of-run summary ────────────────────────────────────────────────
        tracker = MetricsTracker(session)
        summary = tracker.summarize(run_id)
        mttr    = tracker.compute_mttr(run_id)
        _print_summary(summary, state, mttr)

        # ── User feedback ─────────────────────────────────────────────────────
        score, comment = _collect_feedback()
        run_store.update_run_completed(run_id, feedback_score=score, feedback_comment=comment)

        print(f"\nRun {run_id} completed. Thank you for your feedback.")

    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
    finally:
        session.close()


# ── handle_approve ────────────────────────────────────────────────────────────


def handle_approve(run_id: str, gate: str, token: str) -> None:
    """Resume a paused run after human gate approval (cross-process, requires PostgreSQL)."""
    if not _LANGGRAPH_AVAILABLE:
        print("ERROR: langgraph not installed.")
        print("  Run: pip install langgraph")
        sys.exit(1)

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        print("  Cross-process approval requires PostgreSQL and DATABASE_URL.")
        print("  For same-process interactive approval, use: python -m orchestrator.run run ...")
        sys.exit(1)

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        import psycopg
    except ImportError:
        print("ERROR: PostgresSaver requires psycopg and langgraph[postgres].")
        print("  Run: pip install langgraph[postgres] psycopg[binary]")
        sys.exit(1)

    from orchestrator.agents.stage_agent import StageAgent
    from orchestrator.cache.response_cache import ResponseCache
    from orchestrator.core.engine import OrchestrationEngine
    from orchestrator.gateway.auth import TokenAuthenticator
    from orchestrator.gateway.gateway import AIGateway
    from orchestrator.governance.audit import AuditLogger
    from orchestrator.governance.checkpoint import RBACCheckpoint
    from orchestrator.memory.store import MemoryStore
    from orchestrator.scenarios.greenfield import GreenFieldScenario
    from orchestrator.scenarios.nodes import make_gate_node, make_stage_node
    from orchestrator.state.store import RunStateStore
    from orchestrator.tools.registry import ToolRegistry
    from service.db.session import SessionLocal

    session = SessionLocal()
    try:
        run_store = RunStateStore(session)
        audit     = AuditLogger(session)
        auth      = TokenAuthenticator()
        rbac      = RBACCheckpoint(auth)

        # Load run to get triggered_by for four-eyes check
        run_row = run_store.get_run(run_id)
        triggered_by = run_row["triggered_by"]

        # Verify approver identity + permission + four-eyes
        gate_node_name = _GATE_CHOICES[gate]
        required_permission = _GATE_PERMISSIONS[gate_node_name]
        try:
            approver_login = rbac.request_approval(
                run_id=run_id,
                gate_name=gate_node_name,
                required_permission=required_permission,
                trigger_user=triggered_by,
                approver_token=token,
            )
        except Exception as exc:
            print(f"Approval denied: {exc}")
            sys.exit(1)

        comment = input(f"Comment for gate '{gate}' (optional): ").strip()
        decision = input("Approve? [y/n]: ").strip().lower()
        approved = decision == "y"

        human_input = {"approved": approved, "approver": approver_login, "comment": comment}

        # Rebuild engine with PostgresSaver so it can resume the saved checkpoint
        gateway  = AIGateway()
        registry = ToolRegistry()
        memory_store = MemoryStore(session)
        cache    = ResponseCache(session)
        agent    = StageAgent(gateway, registry, cache)

        all_node_names = GreenFieldScenario.node_names()
        gate_names  = {n for n in all_node_names if n.endswith("_gate")}
        stage_names = [n for n in all_node_names if n not in gate_names]

        nodes: dict[str, Any] = {}
        for name in stage_names:
            nodes[name] = make_stage_node(name, agent, memory_store, run_store, audit)
        for name in gate_names:
            nodes[name] = make_gate_node(name, _GATE_PERMISSIONS[name], audit, hybrid_gate=None)

        with psycopg.connect(database_url, autocommit=True) as conn:
            checkpointer = PostgresSaver(conn)
            engine = OrchestrationEngine(
                scenario=GreenFieldScenario(nodes),
                checkpointer=checkpointer,
            )
            result = engine.resume(run_id, human_input)

        status = "approved" if approved else "rejected"
        print(f"\nGate '{gate}' {status} by {approver_login}.")
        if not approved:
            run_store.update_run_status(run_id, "rejected")

    finally:
        session.close()


# ── Gate loop ─────────────────────────────────────────────────────────────────


def _run_gate_loop(
    engine: Any,
    initial_state: Any,
    triggered_by: str,
    rbac: Any,
    run_store: Any,
) -> None:
    """Execute the engine, pausing at each interrupt() gate to collect approval.

    LangGraph's interrupt() causes invoke() to return with the current state.
    engine.get_state(run_id).next is non-empty when a gate is pending.
    engine.resume(run_id, human_input) continues from the interrupt point.
    """
    run_id = initial_state["run_id"]
    first = True
    human_input: dict = {}

    while True:
        if first:
            engine.run(initial_state)
            first = False
        else:
            engine.resume(run_id, human_input)

        snapshot = engine.get_state(run_id)
        if not snapshot.next:
            print("\nAll pipeline stages completed.")
            return

        # Extract interrupt payload from the pending task
        gate_info = _extract_interrupt(snapshot)
        if gate_info is None:
            print("\nPipeline paused (no interrupt payload). Use 'approve' command.")
            return

        gate_name           = gate_info.get("gate_name", "unknown_gate")
        required_permission = gate_info.get("required_permission", "")

        print(f"\n{'='*60}")
        print(f"  GATE REACHED: {gate_name}")
        print(f"  Required permission: {required_permission}")
        _print_artifacts_summary(gate_info.get("stage_artifacts", {}))
        print(f"{'='*60}")

        while True:
            approver_token = input("  Approver token: ").strip()
            try:
                approver_login = rbac.request_approval(
                    run_id=run_id,
                    gate_name=gate_name,
                    required_permission=required_permission,
                    trigger_user=triggered_by,
                    approver_token=approver_token,
                )
                break
            except Exception as exc:
                print(f"  ERROR: {exc}")
                print("  Try a different token.\n")

        comment = input("  Comment (optional, press Enter to skip): ").strip()
        decision = input("  Approve? [y/n]: ").strip().lower()
        approved = decision == "y"

        human_input = {"approved": approved, "approver": approver_login, "comment": comment}
        print(f"  {'Approved' if approved else 'Rejected'} by {approver_login}.")

        if not approved:
            print(f"\nGate '{gate_name}' rejected. Run stopped.")
            run_store.update_run_status(run_id, "rejected")
            return


def _extract_interrupt(snapshot: Any) -> dict | None:
    """Pull the interrupt payload from the first interrupted task in the snapshot."""
    tasks = getattr(snapshot, "tasks", None) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or []
        for intr in interrupts:
            value = getattr(intr, "value", None)
            if isinstance(value, dict):
                return value
    return None


# ── Display helpers ───────────────────────────────────────────────────────────


def _print_artifacts_summary(artifacts: dict) -> None:
    if not artifacts:
        return
    print(f"  Completed stages: {', '.join(artifacts.keys())}")


def _print_summary(summary: dict, state: Any, mttr: float | None) -> None:
    run_id = summary["run_id"]
    print(f"\n{'='*60}")
    print(f"  RUN SUMMARY — {run_id}")
    print(f"{'='*60}")
    print(f"  Requirement : {state.get('requirement', '')[:72]}")
    print(f"  Scenario    : {state.get('scenario_type', '')}")
    print(f"  Cost (USD)  : ${summary['total_cost_usd']:.4f}")
    print(f"  Tokens      : {summary['total_tokens']:,}")
    print(f"  Cache hits  : {summary['cache_hit_rate']*100:.1f}%")
    print(f"  Avg latency : {summary['avg_llm_latency_ms']:.0f} ms")
    print(f"  Stages done : {summary['stages_completed']}")
    print(f"  Stages fail : {summary['stages_failed']}")
    print(f"  Retries     : {summary['retry_count']}")
    if mttr is not None:
        print(f"  MTTR        : {mttr:.1f}s")
    print(f"{'='*60}")


def _collect_feedback() -> tuple[int | None, str | None]:
    """Prompt the user for a 1–4 satisfaction score and optional comment."""
    print("\nHow satisfied are you with this run's output?")
    print("  1 = Unusable   2 = Needs work   3 = Good   4 = Excellent")
    raw = input("Score [1-4] (or press Enter to skip): ").strip()
    if not raw:
        return None, None
    try:
        score = int(raw)
        if score not in (1, 2, 3, 4):
            return None, None
    except ValueError:
        return None, None
    comment = input("Comment (optional): ").strip() or None
    return score, comment


# ── Module entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()

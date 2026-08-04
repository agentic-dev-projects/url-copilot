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

from dotenv import load_dotenv
load_dotenv()  # load .env before AIGateway constructs openai.OpenAI()

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

    # ── review ────────────────────────────────────────────────────────────────
    review_p = sub.add_parser("review", help="List pending gate approvals you can action")
    review_p.add_argument("--token", required=True,
                          help="Approver auth token (see orchestrator/config/users.yaml)")

    # ── approve ───────────────────────────────────────────────────────────────
    approve_p = sub.add_parser("approve", help="Review artifacts and approve a paused gate")
    approve_p.add_argument("--run-id", required=True,
                           help="Run ID shown by 'run' or 'review' command")
    approve_p.add_argument("--token", required=True,
                           help="Approver auth token (must differ from run submitter)")

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
    elif args.command == "review":
        handle_review(args.token)
    elif args.command == "approve":
        handle_approve(args.run_id, args.token)


# ── handle_run ────────────────────────────────────────────────────────────────


def handle_run(requirement: str, token: str) -> None:
    """Classify, plan, start the pipeline, and exit once the first gate is reached."""
    if not _LANGGRAPH_AVAILABLE:
        print("ERROR: langgraph not installed.")
        print("  Run: pip install langgraph")
        sys.exit(1)

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        import psycopg
    except ImportError:
        print("ERROR: PostgresSaver requires psycopg and langgraph[postgres].")
        print("  Run: pip install 'psycopg[binary]' 'langgraph[postgres]'")
        sys.exit(1)

    from orchestrator.agents.stage_agent import StageAgent
    from orchestrator.cache.response_cache import ResponseCache
    from orchestrator.core.engine import OrchestrationEngine
    from orchestrator.gateway.auth import TokenAuthenticator
    from orchestrator.gateway.gateway import AIGateway
    from orchestrator.governance.audit import AuditLogger
    from orchestrator.memory.store import MemoryStore
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
        run_store    = RunStateStore(session)
        audit        = AuditLogger(session)
        memory_store = MemoryStore(session)
        cache        = ResponseCache(session)
        registry     = ToolRegistry()
        gateway      = AIGateway()
        auth         = TokenAuthenticator()

        memory_store.seed_if_empty()

        user = auth.resolve(token)
        print(f"\nAuthenticated as: {user.github_login} ({user.role})")

        classifier    = RequirementClassifier(gateway)
        clarification = ClarificationLoop(gateway)
        planner       = Planner(classifier, clarification, run_store)

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

        agent = StageAgent(gateway, registry, cache)
        nodes = _build_nodes(agent, memory_store, run_store, audit)

        scenario_cls = {
            "greenfield": GreenFieldScenario,
            "brownfield": BrownfieldScenario,
            "ambiguous":  AmbiguousScenario,
        }.get(state["scenario_type"], GreenFieldScenario)

        print(f"\nStarting pipeline for run {run_id}...")
        print("-" * 60)

        with psycopg.connect(database_url, autocommit=True) as conn:
            checkpointer = PostgresSaver(conn)
            checkpointer.setup()
            engine = OrchestrationEngine(
                scenario=scenario_cls(nodes),
                checkpointer=checkpointer,
            )
            engine.run(state)

            snapshot = engine.get_state(run_id)

        if not snapshot.next:
            # Pipeline completed without hitting any gate (unusual)
            run_store.update_run_status(run_id, "completed")
            print(f"\nPipeline completed for run {run_id}.")
            return

        gate_info = _extract_interrupt(snapshot)
        gate_name = gate_info.get("gate_name", "unknown_gate") if gate_info else "unknown_gate"
        run_store.update_run_status(run_id, f"awaiting:{gate_name}")

        print(f"\nPipeline paused — waiting for gate approval.")
        print(f"\n  Run ID : {run_id}")
        print(f"  Gate   : {gate_name}  (requires: {_GATE_PERMISSIONS.get(gate_name, '?')})")
        print(f"\nNext steps:")
        print(f"  # Approver — see all pending reviews:")
        print(f"  python -m orchestrator.run review --token <approver_token>")
        print(f"\n  # Approver — approve this specific run:")
        print(f"  python -m orchestrator.run approve --run-id {run_id} --token <approver_token>")

    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
    finally:
        session.close()


# ── handle_review ─────────────────────────────────────────────────────────────


def handle_review(token: str) -> None:
    """List all pending gate approvals the caller has permission to action."""
    from orchestrator.gateway.auth import TokenAuthenticator
    from orchestrator.governance.checkpoint import RBACCheckpoint
    from orchestrator.state.store import RunStateStore
    from service.db.session import SessionLocal

    session = SessionLocal()
    try:
        run_store = RunStateStore(session)
        auth      = TokenAuthenticator()
        rbac      = RBACCheckpoint(auth)

        user = auth.resolve(token)
        print(f"\nPending gate approvals  —  logged in as: {user.github_login} ({user.role})")

        pending = run_store.get_pending_runs()
        if not pending:
            print("\n  No runs are currently awaiting approval.")
            return

        approvable = []
        for run in pending:
            raw_status = run.get("status", "")
            if not raw_status.startswith("awaiting:"):
                continue
            gate_name = raw_status.split(":", 1)[1]
            required_permission = _GATE_PERMISSIONS.get(gate_name)
            if not required_permission:
                continue
            # Check four-eyes and permission without raising
            try:
                rbac.request_approval(
                    run_id=run["id"],
                    gate_name=gate_name,
                    required_permission=required_permission,
                    trigger_user=run["triggered_by"],
                    approver_token=token,
                )
                approvable.append((run, gate_name))
            except Exception:
                pass  # Not permitted or four-eyes violation — skip silently

        if not approvable:
            print(f"\n  No pending approvals for your role ({user.role}).")
            print("  Either no runs are waiting, or you submitted all of them (four-eyes).")
            return

        print(f"\n  {'RUN ID':<18} {'GATE':<25} {'SUBMITTED BY':<14} REQUIREMENT")
        print(f"  {'-'*18} {'-'*25} {'-'*14} {'-'*40}")
        for run, gate_name in approvable:
            req_short = (run["requirement"] or "")[:40]
            print(f"  {run['id']:<18} {gate_name:<25} {run['triggered_by']:<14} {req_short}")

        print(f"\nTo review and approve:")
        print(f"  python -m orchestrator.run approve --run-id <RUN_ID> --token {token}")

    finally:
        session.close()


# ── handle_approve ────────────────────────────────────────────────────────────


def handle_approve(run_id: str, token: str) -> None:
    """Review artifacts for a pending gate and approve or reject it."""
    if not _LANGGRAPH_AVAILABLE:
        print("ERROR: langgraph not installed.")
        print("  Run: pip install langgraph")
        sys.exit(1)

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        import psycopg
    except ImportError:
        print("ERROR: PostgresSaver requires psycopg and langgraph[postgres].")
        print("  Run: pip install 'psycopg[binary]' 'langgraph[postgres]'")
        sys.exit(1)

    from orchestrator.agents.stage_agent import StageAgent
    from orchestrator.cache.response_cache import ResponseCache
    from orchestrator.core.engine import OrchestrationEngine
    from orchestrator.gateway.auth import TokenAuthenticator
    from orchestrator.gateway.gateway import AIGateway
    from orchestrator.governance.audit import AuditLogger
    from orchestrator.governance.checkpoint import RBACCheckpoint
    from orchestrator.memory.store import MemoryStore
    from orchestrator.metrics.tracker import MetricsTracker
    from orchestrator.scenarios.ambiguous import AmbiguousScenario
    from orchestrator.scenarios.brownfield import BrownfieldScenario
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

        # ── Discover pending gate from DB ─────────────────────────────────────
        run_row = run_store.get_run(run_id)
        raw_status = run_row.get("status", "")
        if not raw_status.startswith("awaiting:"):
            print(f"Run {run_id} is not awaiting approval (status: {raw_status}).")
            sys.exit(1)

        gate_name = raw_status.split(":", 1)[1]
        required_permission = _GATE_PERMISSIONS.get(gate_name)
        if not required_permission:
            print(f"Unknown gate '{gate_name}' — no permission mapping found.")
            sys.exit(1)

        # ── Authenticate + RBAC ───────────────────────────────────────────────
        try:
            approver_login = rbac.request_approval(
                run_id=run_id,
                gate_name=gate_name,
                required_permission=required_permission,
                trigger_user=run_row["triggered_by"],
                approver_token=token,
            )
        except Exception as exc:
            print(f"Approval denied: {exc}")
            sys.exit(1)

        print(f"\nAuthenticated as: {approver_login}")
        print(f"Run ID     : {run_id}")
        print(f"Gate       : {gate_name}")
        print(f"Submitted by: {run_row['triggered_by']}")
        print(f"\nRequirement: {run_row['requirement']}")

        # ── Load stage artifacts from DB for display ──────────────────────────
        stage_results = run_store.get_all_stage_results(run_id)
        artifacts = {
            r["stage_name"]: r["output_artifact"]
            for r in stage_results
            if r.get("output_artifact") is not None
        }

        print(f"\n{'='*60}")
        _print_gate_context(gate_name, artifacts)
        print(f"{'='*60}")

        # ── Collect decision ──────────────────────────────────────────────────
        comment = input("\nReview comment (optional, press Enter to skip): ").strip()
        decision = input("Approve? [y/n]: ").strip().lower()
        approved = decision == "y"

        if not approved:
            run_store.update_run_status(run_id, "rejected")
            print(f"\nGate '{gate_name}' rejected by {approver_login}. Run stopped.")
            return

        human_input = {"approved": True, "approver": approver_login, "comment": comment}

        # ── Resume the graph ──────────────────────────────────────────────────
        memory_store = MemoryStore(session)
        cache        = ResponseCache(session)
        registry     = ToolRegistry()
        gateway      = AIGateway()
        agent        = StageAgent(gateway, registry, cache)
        nodes        = _build_nodes(agent, memory_store, run_store, audit)

        scenario_type = run_row.get("scenario_type", "greenfield")
        scenario_cls = {
            "greenfield": GreenFieldScenario,
            "brownfield": BrownfieldScenario,
            "ambiguous":  AmbiguousScenario,
        }.get(scenario_type, GreenFieldScenario)

        print(f"\nApproved by {approver_login}. Resuming pipeline...")
        print("-" * 60)

        with psycopg.connect(database_url, autocommit=True) as conn:
            checkpointer = PostgresSaver(conn)
            engine = OrchestrationEngine(
                scenario=scenario_cls(nodes),
                checkpointer=checkpointer,
            )
            engine.resume(run_id, human_input)
            snapshot = engine.get_state(run_id)

        # ── Handle post-approval state ────────────────────────────────────────
        if not snapshot.next:
            # Pipeline completed
            run_store.update_run_status(run_id, "running")  # will be updated below
            tracker = MetricsTracker(session)
            summary = tracker.summarize(run_id)
            mttr    = tracker.compute_mttr(run_id)
            state_view = {"run_id": run_id, "requirement": run_row["requirement"],
                          "scenario_type": scenario_type}
            _print_summary(summary, state_view, mttr)
            score, feedback_comment = _collect_feedback()
            run_store.update_run_completed(run_id, feedback_score=score,
                                           feedback_comment=feedback_comment)
            print(f"\nRun {run_id} completed.")
            return

        # Pipeline paused at the next gate
        next_gate_info = _extract_interrupt(snapshot)
        next_gate = next_gate_info.get("gate_name", "unknown_gate") if next_gate_info else "unknown_gate"
        run_store.update_run_status(run_id, f"awaiting:{next_gate}")

        print(f"\nPipeline paused at next gate: {next_gate}")
        print(f"  Required permission: {_GATE_PERMISSIONS.get(next_gate, '?')}")
        print(f"\nTo continue:")
        print(f"  python -m orchestrator.run review --token <approver_token>")
        print(f"  python -m orchestrator.run approve --run-id {run_id} --token <approver_token>")

    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
    finally:
        session.close()


# ── Gate loop ─────────────────────────────────────────────────────────────────


def _build_nodes(agent: Any, memory_store: Any, run_store: Any, audit: Any) -> dict[str, Any]:
    """Build the node functions dict for all stage and gate nodes."""
    from orchestrator.scenarios.greenfield import GreenFieldScenario
    from orchestrator.scenarios.nodes import make_gate_node, make_stage_node

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
            hybrid_gate=None,
        )
    return nodes


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


def _print_gate_context(gate_name: str, artifacts: dict) -> None:
    """Print the artifact content most relevant to the approver for this gate."""
    if not artifacts:
        print("  (no stage artifacts available)")
        return

    import json

    if gate_name == "architecture_gate":
        _show_artifact("REQUIREMENTS ANALYSIS", artifacts.get("requirements_analysis"))
        _show_artifact("ARCHITECTURE DESIGN", artifacts.get("architecture_design"))

    elif gate_name == "schema_gate":
        arch = artifacts.get("architecture_design")
        if arch and isinstance(arch, dict):
            print(f"\n  MIGRATION REVIEW")
            notes = arch.get("migration_notes") or arch.get("migrationNotes") or arch.get("migration")
            print(f"  Migration notes: {notes or '(see architecture design below)'}")
        _show_artifact("ARCHITECTURE DESIGN", arch)

    elif gate_name == "tests_gate":
        _show_artifact("UNIT TESTS", artifacts.get("unit_tests"))
        _show_artifact("INTEGRATION TESTS", artifacts.get("integration_tests"))

    elif gate_name in ("pr_gate", "release_gate"):
        _show_artifact("RELEASE READINESS", artifacts.get("release_readiness"))

    else:
        for stage, artifact in artifacts.items():
            if not stage.endswith("_gate"):
                _show_artifact(stage.upper().replace("_", " "), artifact)


def _show_artifact(label: str, artifact: Any) -> None:
    """Print a stage artifact — pretty-printed JSON so reviewers see the full content."""
    import json
    print(f"\n  {'─'*56}")
    print(f"  {label}")
    print(f"  {'─'*56}")
    if artifact is None:
        print("  (not available)")
        return
    if isinstance(artifact, dict):
        # Pretty-print each top-level key for readability
        for key, value in artifact.items():
            label_key = key.replace("_", " ").upper()
            if isinstance(value, list):
                if value:
                    print(f"\n  {label_key}:")
                    for item in value:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                print(f"    {k}: {v}")
                            print()
                        else:
                            print(f"    • {item}")
                else:
                    print(f"\n  {label_key}: (none)")
            elif isinstance(value, dict):
                print(f"\n  {label_key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            elif isinstance(value, bool):
                print(f"\n  {label_key}: {'YES ⚠' if value else 'No'}")
            elif value:
                print(f"\n  {label_key}:\n  {value}")
    else:
        print(f"  {artifact}")


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

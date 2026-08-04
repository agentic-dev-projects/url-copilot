"""
Planner — orchestrates classification, optional clarification, and run creation.

The Planner is the entry point called by the CLI before handing off to the
OrchestrationEngine.  It produces a fully initialised OrchestratorState that
the engine can invoke immediately.

Three-step process
------------------
1. Classify  — RequirementClassifier determines greenfield / brownfield / ambiguous.
2. Clarify   — If ambiguous, ClarificationLoop runs a CLI Q&A to resolve the
               requirement into an unambiguous scope.
3. Initialise — Planner generates a run_id, creates the orch_runs DB record (if a
               RunStateStore is provided), and builds the initial OrchestratorState.

Why run_store is optional
-------------------------
Tests and the engine-only invocation path do not need a live PostgreSQL session.
Passing run_store=None skips the DB write; the engine still runs correctly because
it uses LangGraph's own checkpointer (MemorySaver in tests, PostgresSaver in prod).
The CLI entry point (run.py) always provides a run_store so the business record
exists before the engine starts.

run_id format
-------------
"orch-{8 hex chars}" — short, human-readable, and unique enough for a developer
workflow tool.  Example: "orch-a3f8c12e".  UUID4 entropy ensures no collisions.

Protocol pattern
----------------
Planner accepts _StoreCallable and the classifier/clarification objects as
constructor arguments so all dependencies can be replaced with MagicMocks in tests.
"""

import uuid
from typing import Any, Protocol

from orchestrator.core.state import OrchestratorState
from orchestrator.planner.clarification import ClarificationLoop, ClarificationResult
from orchestrator.planner.classifier import ClassifierResult, RequirementClassifier


class _RunStore(Protocol):
    def create_run(
        self,
        run_id: str,
        requirement: str,
        scenario_type: str,
        triggered_by: str,
    ) -> None: ...


class Planner:
    """Classifies a requirement, optionally runs clarification, and returns an OrchestratorState."""

    def __init__(
        self,
        classifier: RequirementClassifier,
        clarification_loop: ClarificationLoop,
        run_store: _RunStore | None = None,
    ) -> None:
        """
        Args:
            classifier:         RequirementClassifier for classify().
            clarification_loop: ClarificationLoop to resolve ambiguous requirements.
            run_store:          Optional RunStateStore; if provided, creates the
                                orch_runs record before the engine starts.
        """
        self._classifier = classifier
        self._clarification = clarification_loop
        self._run_store = run_store

    def plan(
        self,
        requirement: str,
        triggered_by: str,
        token: str,
    ) -> OrchestratorState:
        """Classify, optionally clarify, and return a ready-to-run OrchestratorState.

        Args:
            requirement:  Raw user requirement string.
            triggered_by: github_login of the user starting the run.
            token:        Auth token passed through to classifier and clarification calls.

        Returns:
            OrchestratorState with run_id, requirement, resolved_requirement,
            scenario_type, triggered_by, assumptions, and empty collections
            for stage_artifacts, tool_cache, etc.
        """
        run_id = self._generate_run_id()

        classification: ClassifierResult = self._classifier.classify(
            requirement=requirement,
            token=token,
            run_id=run_id,
        )

        resolved_requirement = requirement
        assumptions: list[str] = []

        if classification.scenario_type == "ambiguous":
            clarification: ClarificationResult = self._clarification.run(
                requirement=requirement,
                token=token,
                run_id=run_id,
            )
            resolved_requirement = clarification.resolved_requirement
            assumptions = clarification.assumptions

        if self._run_store is not None:
            self._run_store.create_run(
                run_id=run_id,
                requirement=requirement,
                scenario_type=classification.scenario_type,
                triggered_by=triggered_by,
            )

        return OrchestratorState(
            run_id=run_id,
            requirement=requirement,
            resolved_requirement=resolved_requirement,
            scenario_type=classification.scenario_type,
            triggered_by=triggered_by,
            stage_artifacts={},
            stage_evaluations={},
            assumptions=assumptions,
            tool_cache={},
            feature_branch=None,
            pr_url=None,
            pr_number=None,
            schema_change_detected=False,
        )

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _generate_run_id() -> str:
        return f"orch-{uuid.uuid4().hex[:8]}"

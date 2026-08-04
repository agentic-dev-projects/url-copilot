"""
orchestrator.core.stage — Stage vocabulary: status, node definition, execution result.

These three types are the building blocks every other component uses to describe
pipeline stages.  They are pure dataclasses with no external dependencies, making
them safe to import anywhere without circular-import risk.

Types
-----
StageStatus     Enum of all possible states a stage can be in.  Using an enum
                instead of string literals prevents typos and makes IDE
                auto-complete work across the codebase.

StageNode       Describes a stage BEFORE it runs: its name, what it depends on,
                whether it requires a human approval gate, and how many times
                the engine should retry it before giving up.  Immutable once
                built by a Scenario.

StageResult     Describes a stage AFTER it runs: what artifact it produced,
                which model and prompt version were used, how long it took,
                and whether it failed.  Persisted to orch_stage_results by
                RunStateStore (Phase 4).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class StageStatus(Enum):
    """Lifecycle states for a single pipeline stage.

    Transitions:
        PENDING → RUNNING → COMPLETED
                          → FAILED (after max_attempts exhausted)
                          → AWAITING_APPROVAL (gate reached, waiting for human)
        PENDING → SKIPPED  (conditional gate not triggered, e.g. Gate #2 schema change)
        COMPLETED → ROLLED_BACK (operator rollback via CLI)
    """
    PENDING           = "pending"
    RUNNING           = "running"
    COMPLETED         = "completed"
    FAILED            = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    SKIPPED           = "skipped"
    ROLLED_BACK       = "rolled_back"


@dataclass
class StageNode:
    """Immutable description of a pipeline stage — built once by a Scenario.

    Attributes
    ----------
    name            Unique stage identifier, e.g. "architecture_design".
                    Used as the key in RunContext.stage_artifacts and as the
                    stage_name column in all orch_ DB tables.
    depends_on      List of stage names that must be COMPLETED before this
                    stage is eligible to run.  Empty list = no dependencies
                    (the stage is immediately ready at run start).
    status          Current lifecycle state.  Mutated by OrchestrationEngine
                    as the run progresses.
    requires_gate   Permission string that must be held by the approver to pass
                    the human gate after this stage completes.  None = no gate.
                    Example: "approve_architecture" for ARCHITECTURE_DESIGN.
    max_attempts    How many times the engine will retry this stage on failure
                    before marking it FAILED and pausing the run.
    """
    name: str
    depends_on: list[str]     = field(default_factory=list)
    status: StageStatus       = StageStatus.PENDING
    requires_gate: str | None = None
    max_attempts: int         = 3


@dataclass
class StageResult:
    """Everything produced by one execution attempt of a stage.

    Persisted to orch_stage_results after each attempt (including failures,
    so retries are fully traceable).  The combination of run_id + stage_name
    + attempt_number uniquely identifies any execution in the audit trail.

    Attributes
    ----------
    stage_name      Matches StageNode.name.
    status          Final status of this attempt.
    attempt_number  1-based retry counter.  attempt_number=2 means the stage
                    failed once and this is the first retry.
    started_at      Wall-clock time the stage agent began execution.
    completed_at    Wall-clock time the stage finished (success or failure).
                    None if the stage is still running.
    output_artifact Structured JSON output from the LLM for this stage.
                    Shape varies by stage (architecture artifact vs test plan,
                    etc.) — stored as JSONB in orch_stage_results.
    error_message   Exception message if the stage failed.  None on success.
    prompt_version  Which prompt file was used, e.g. "architecture_v1".
                    Recorded for reproducibility and cost attribution.
    model_used      Which OpenAI model served this call, e.g. "gpt-4o".
                    Recorded for cost attribution and quality tracking.
    """
    stage_name:       str
    status:           StageStatus
    attempt_number:   int
    started_at:       datetime
    completed_at:     datetime | None        = None
    output_artifact:  dict[str, Any] | None  = None
    error_message:    str | None             = None
    prompt_version:   str | None             = None
    model_used:       str | None             = None

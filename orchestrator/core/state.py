"""
orchestrator.core.state — LangGraph state definition for the orchestrator.

OrchestratorState is the TypedDict that LangGraph uses as the shared state
passed between every node in the pipeline graph.  It replaces the RunContext
dataclass from the original design.

Why TypedDict instead of dataclass?
------------------------------------
LangGraph requires state to be a TypedDict (or Pydantic BaseModel).  At each
node, the function returns a partial dict — only the keys it modified — and
LangGraph merges that update into the full state using its built-in reducer.
This is more efficient than passing and copying a full dataclass on every node.

Annotated reducers for concurrent fan-out
-----------------------------------------
stage_artifacts, stage_evaluations, and tool_cache are written by concurrent
fan-out nodes (e.g. implementation_plan + test_plan both update stage_artifacts
in the same LangGraph step).  LangGraph requires an Annotated[..., reducer]
on any key written by multiple concurrent nodes — without it, LangGraph raises:
  InvalidUpdateError: Can receive only one value per step.

operator.or_ performs Python 3.9+ dict merge (equivalent to {**a, **b}).
The later node's value wins on key collision, which is the desired behaviour
since each concurrent node writes a different stage_name key.

How LangGraph uses this
-----------------------
1. Planner creates the initial state dict and calls graph.invoke(state, config).
2. Each node function receives the full OrchestratorState and returns a dict
   with only the keys it changed.
3. LangGraph merges the update, checkpoints the new state to PostgreSQL via
   PostgresSaver, and advances to the next ready node(s).
4. At interrupt() nodes (human gates), LangGraph serializes state to DB and
   pauses.  Resuming is: graph.invoke(Command(resume=...), config) with the
   same thread_id.
"""

import operator
from typing import Annotated, Any, TypedDict


class OrchestratorState(TypedDict, total=False):
    """LangGraph state shared across all pipeline nodes."""
    run_id:                 str
    requirement:            str
    resolved_requirement:   str
    scenario_type:          str
    triggered_by:           str
    token:                  str
    stage_artifacts:        Annotated[dict[str, Any], operator.or_]
    stage_evaluations:      Annotated[dict[str, Any], operator.or_]
    feature_branch:         str | None
    pr_url:                 str | None
    pr_number:              int | None
    schema_change_detected: bool
    assumptions:            list[str]
    tool_cache:             Annotated[dict[str, Any], operator.or_]

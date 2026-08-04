"""
AmbiguousScenario — pipeline for unclear or under-specified requirements.

The Planner (Phase 14) detects ambiguous intent and runs a clarification loop
BEFORE this scenario's graph is invoked.  By the time build_graph() is called,
OrchestratorState already contains:
  - resolved_requirement: the scoped, unambiguous version of the requirement
  - assumptions: list of decisions made during clarification
  - scenario_type: "ambiguous" (so prompts know to reference resolved_requirement)

The first node (requirements_analysis) reads state["resolved_requirement"]
instead of state["requirement"], ensuring the full pipeline works on the
clarified scope.

Same topology as GreenField — ambiguity is resolved in planning, not by
changing the execution graph.
"""

from typing import Any

from orchestrator.scenarios.greenfield import GreenFieldScenario


class AmbiguousScenario(GreenFieldScenario):
    """Pipeline for requirements clarified via the pre-run clarification loop.

    Same 14-node graph topology as GreenFieldScenario.
    The scenario_type="ambiguous" in OrchestratorState signals to
    PromptBuilder that the user message should use resolved_requirement
    (the clarified scope) rather than the original vague requirement.
    """

    def build_graph(self, checkpointer: Any):
        """Build the ambiguous pipeline — same topology, pre-clarified state."""
        return super().build_graph(checkpointer)

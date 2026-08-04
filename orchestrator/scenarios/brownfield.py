"""
BrownfieldScenario — pipeline for modifying or extending existing features.

Identical graph topology to GreenFieldScenario.  The difference is in the
prompt context: brownfield nodes receive a "brownfield" scenario_type in
OrchestratorState, which causes PromptBuilder to include more codebase context
(reading related existing files) and the stage prompts to emphasise preserving
backwards compatibility.

This separation keeps the topology clean — a future topology change that
only affects brownfield (e.g. a mandatory "compatibility check" node) can be
added here without touching GreenFieldScenario.
"""

from typing import Any, Callable

from orchestrator.scenarios.greenfield import GreenFieldScenario


class BrownfieldScenario(GreenFieldScenario):
    """Pipeline for modifying existing features.

    Inherits the same 14-node graph topology as GreenFieldScenario.
    The scenario_type="brownfield" in OrchestratorState is the signal
    that causes the stage prompts to emphasise reading existing code
    before proposing changes.
    """

    def build_graph(self, checkpointer: Any):
        """Build the brownfield pipeline — same topology as greenfield."""
        return super().build_graph(checkpointer)

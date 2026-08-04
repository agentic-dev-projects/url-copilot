"""
BaseScenario — abstract base for all orchestrator pipeline scenarios.

Each scenario defines a LangGraph StateGraph topology — which nodes run,
in what order, with which fan-out/fan-in patterns, and where interrupt()
gates pause execution for human approval.

The engine (Phase 13) calls scenario.build_graph(saver) and invokes the
compiled graph — it has no knowledge of the topology itself.

Why one class per scenario instead of one graph with conditionals?
------------------------------------------------------------------
The three scenarios (greenfield, brownfield, ambiguous) share the same
STAGE LOGIC but differ in their graph TOPOLOGY and their initial state
shape (brownfield reads existing code; ambiguous has a resolved_requirement
from the clarification loop).  Keeping them as separate classes means:

  1. Each topology is independently testable.
  2. Adding a new scenario (e.g. "hotfix") doesn't require touching
     existing scenario graphs.
  3. The declarative edge lists make topology easy to review in a PR.

Saver injection
---------------
build_graph() takes a checkpointer (MemorySaver in tests, PostgresSaver in
production).  This keeps scenarios testable without a PostgreSQL connection.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseScenario(ABC):
    """Abstract base for orchestrator pipeline scenarios."""

    @abstractmethod
    def build_graph(self, checkpointer: Any):
        """Build and compile the LangGraph StateGraph for this scenario.

        Args:
            checkpointer: A LangGraph checkpointer (MemorySaver or PostgresSaver).
                          Passed at construction time so production and test code
                          use the same build_graph() method.

        Returns:
            A compiled LangGraph graph (CompiledGraph) ready for .invoke().
        """
        raise NotImplementedError

"""
orchestrator.core.dag — Directed Acyclic Graph for pipeline stage dependencies.

DAGGraph encodes the SDLC pipeline as a dependency graph.  The OrchestrationEngine
calls get_ready_stages() in its main loop to find which stages are eligible to run
right now (all upstream dependencies COMPLETED), runs them concurrently if there
are multiple, waits at sync points, and repeats until the DAG is complete.

Why a DAG?
----------
A simple list forces sequential execution.  A DAG lets the Planner express
"IMPLEMENTATION_PLAN and TEST_PLAN have no dependency on each other — run them
in parallel."  This cuts wall-clock time significantly on multi-step pipelines.
The engine doesn't need to know the topology — it just asks the DAG "what's ready?"
and the DAG handles the dependency math.

Correctness guarantee
---------------------
DAGGraph enforces two invariants:
  1. No duplicate stage names (add_node raises ValueError on collision).
  2. Every name in depends_on must correspond to a registered node (add_edge
     raises ValueError on unknown stage names).
These checks catch Scenario configuration bugs at DAG-build time, not at runtime.

Usage pattern (in Scenario.build_dag())
-----------------------------------------
    dag = DAGGraph()
    dag.add_node(StageNode("requirements_analysis"))
    dag.add_node(StageNode("architecture_design",
                           depends_on=["requirements_analysis"],
                           requires_gate="approve_architecture"))
    dag.add_node(StageNode("implementation_plan",
                           depends_on=["architecture_design"]))
    dag.add_node(StageNode("test_plan",
                           depends_on=["architecture_design"]))
    # implementation_plan and test_plan are both ready after architecture_design
    # completes → engine runs them concurrently.
"""

from orchestrator.core.stage import StageNode, StageStatus


class DAGGraph:
    """Directed Acyclic Graph over StageNodes.

    Nodes are added via add_node(); edges (dependencies) are inferred from
    each StageNode's depends_on list and validated via add_edge().

    The graph is mutable during a run: mark_completed() and mark_failed()
    update node statuses in place as the engine progresses.
    """

    def __init__(self) -> None:
        """Initialise an empty graph."""
        self._nodes: dict[str, StageNode] = {}

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_node(self, node: StageNode) -> None:
        """Register a stage node in the graph.

        Raises ValueError if a node with the same name already exists.
        Also validates all depends_on names are already registered, so
        call add_node() in topological order within Scenario.build_dag().
        """
        if node.name in self._nodes:
            raise ValueError(f"Duplicate stage name: '{node.name}'")
        for dep in node.depends_on:
            if dep not in self._nodes:
                raise ValueError(
                    f"Stage '{node.name}' depends on unknown stage '{dep}'. "
                    f"Register '{dep}' before '{node.name}'."
                )
        self._nodes[node.name] = node

    def add_edge(self, from_stage: str, to_stage: str) -> None:
        """Add a dependency: to_stage cannot start until from_stage is COMPLETED.

        Equivalent to appending from_stage to to_stage.depends_on.
        Both stages must already be registered.  Raises ValueError otherwise.
        """
        if from_stage not in self._nodes:
            raise ValueError(f"Unknown stage: '{from_stage}'")
        if to_stage not in self._nodes:
            raise ValueError(f"Unknown stage: '{to_stage}'")
        if from_stage not in self._nodes[to_stage].depends_on:
            self._nodes[to_stage].depends_on.append(from_stage)

    # ------------------------------------------------------------------
    # Engine queries
    # ------------------------------------------------------------------

    def get_ready_stages(self) -> list[StageNode]:
        """Return all stages that are eligible to run right now.

        A stage is eligible when:
          - Its status is PENDING (not already running, done, or failed), AND
          - Every stage in its depends_on list has status COMPLETED or SKIPPED.

        The engine runs all returned stages concurrently (parallel execution).
        Returns an empty list when no stages are ready (either all done, or
        the run is stuck waiting for a human gate or a failed stage).
        """
        ready = []
        for node in self._nodes.values():
            if node.status != StageStatus.PENDING:
                continue
            deps_satisfied = all(
                self._nodes[dep].status in (StageStatus.COMPLETED, StageStatus.SKIPPED)
                for dep in node.depends_on
            )
            if deps_satisfied:
                ready.append(node)
        return ready

    def mark_completed(self, stage_name: str) -> None:
        """Transition a stage to COMPLETED.  Raises KeyError for unknown stages."""
        self._get(stage_name).status = StageStatus.COMPLETED

    def mark_skipped(self, stage_name: str) -> None:
        """Transition a stage to SKIPPED (e.g. Gate #2 not triggered)."""
        self._get(stage_name).status = StageStatus.SKIPPED

    def mark_failed(self, stage_name: str) -> None:
        """Transition a stage to FAILED after all retry attempts are exhausted."""
        self._get(stage_name).status = StageStatus.FAILED

    def mark_running(self, stage_name: str) -> None:
        """Transition a stage to RUNNING at the start of an attempt."""
        self._get(stage_name).status = StageStatus.RUNNING

    def mark_awaiting_approval(self, stage_name: str) -> None:
        """Transition a stage to AWAITING_APPROVAL when a gate is reached."""
        self._get(stage_name).status = StageStatus.AWAITING_APPROVAL

    # ------------------------------------------------------------------
    # Completion checks
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """True when every node is COMPLETED or SKIPPED — the run is done."""
        return all(
            n.status in (StageStatus.COMPLETED, StageStatus.SKIPPED)
            for n in self._nodes.values()
        )

    def is_stuck(self) -> bool:
        """True when the run cannot progress without human intervention.

        Stuck means: at least one node FAILED, no nodes currently RUNNING,
        and the DAG is not complete.  The engine pauses and notifies the
        operator when this occurs.
        """
        has_failed  = any(n.status == StageStatus.FAILED for n in self._nodes.values())
        has_running = any(n.status == StageStatus.RUNNING for n in self._nodes.values())
        return has_failed and not has_running and not self.is_complete()

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """Return stage names in a valid execution order (Kahn's algorithm).

        Used for logging and display purposes.  Not used by the engine itself
        (the engine uses get_ready_stages() dynamically instead).
        """
        in_degree: dict[str, int] = {name: 0 for name in self._nodes}
        for node in self._nodes.values():
            for dep in node.depends_on:
                in_degree[node.name] += 1

        queue = [name for name, deg in in_degree.items() if deg == 0]
        result = []
        while queue:
            current = queue.pop(0)
            result.append(current)
            for node in self._nodes.values():
                if current in node.depends_on:
                    in_degree[node.name] -= 1
                    if in_degree[node.name] == 0:
                        queue.append(node.name)
        return result

    def get_node(self, stage_name: str) -> StageNode:
        """Return the StageNode for a given stage name.  Raises KeyError if not found."""
        return self._get(stage_name)

    def all_nodes(self) -> list[StageNode]:
        """Return all registered nodes in insertion order."""
        return list(self._nodes.values())

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get(self, stage_name: str) -> StageNode:
        if stage_name not in self._nodes:
            raise KeyError(f"Unknown stage: '{stage_name}'")
        return self._nodes[stage_name]

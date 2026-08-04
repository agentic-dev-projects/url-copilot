"""
Unit + integration tests for OrchestrationEngine and GreenFieldScenario.

Uses LangGraph's MemorySaver (in-memory checkpointer) — no PostgreSQL needed.
Node functions are simple lambdas that write to stage_artifacts so we can
verify execution order and state merging.

Run: .venv/bin/python -m pytest orchestrator/tests/test_engine.py -v
"""

import pytest

from orchestrator.core.state import OrchestratorState

try:
    from langgraph.checkpoint.memory import MemorySaver
    from orchestrator.core.engine import OrchestrationEngine
    from orchestrator.scenarios.ambiguous import AmbiguousScenario
    from orchestrator.scenarios.brownfield import BrownfieldScenario
    from orchestrator.scenarios.greenfield import GreenFieldScenario
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not LANGGRAPH_AVAILABLE,
    reason="langgraph not installed — run: pip install langgraph",
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> OrchestratorState:
    defaults = dict(
        run_id="run-engine-001",
        requirement="Add a QR code endpoint",
        resolved_requirement="Add GET /api/v1/qr/{short_code}",
        scenario_type="greenfield",
        triggered_by="alice",
        stage_artifacts={},
        assumptions=[],
        tool_cache={},
        schema_change_detected=False,
    )
    defaults.update(kwargs)
    return OrchestratorState(**defaults)


def _counter_node(name: str):
    """Returns a node function that records its own name in stage_artifacts."""
    def node(state: OrchestratorState) -> dict:
        return {"stage_artifacts": {
            **state.get("stage_artifacts", {}),
            name: {"executed": True},
        }}
    node.__name__ = f"{name}_node"
    return node


def _make_nodes(names: list[str]) -> dict:
    return {name: _counter_node(name) for name in names}


# ── GreenFieldScenario: graph structure ──────────────────────────────────────


def test_greenfield_node_names_covers_all_required():
    names = GreenFieldScenario.node_names()
    assert "requirements_analysis" in names
    assert "architecture_gate" in names
    assert "release_gate" in names
    assert len(names) == 14


def test_greenfield_build_graph_returns_compiled_graph():
    nodes = _make_nodes(GreenFieldScenario.node_names())
    scenario = GreenFieldScenario(nodes)
    graph = scenario.build_graph(MemorySaver())
    assert graph is not None


# ── OrchestrationEngine: 3-node linear graph ─────────────────────────────────


def _make_minimal_scenario():
    """Build a minimal 3-node A→B→C GreenField-like scenario for engine tests."""
    from langgraph.graph import END, START, StateGraph

    class MinimalScenario:
        def __init__(self, nodes):
            self._nodes = nodes

        def build_graph(self, checkpointer):
            graph = StateGraph(OrchestratorState)
            for name, fn in self._nodes.items():
                graph.add_node(name, fn)
            graph.add_edge(START, "node_a")
            graph.add_edge("node_a", "node_b")
            graph.add_edge("node_b", "node_c")
            graph.add_edge("node_c", END)
            return graph.compile(checkpointer=checkpointer)

    return MinimalScenario


def test_engine_runs_three_nodes_in_order():
    execution_order = []

    def make_recording_node(name):
        def node(state):
            execution_order.append(name)
            return {"stage_artifacts": {**state.get("stage_artifacts", {}), name: True}}
        node.__name__ = name
        return node

    MinimalScenario = _make_minimal_scenario()
    scenario = MinimalScenario({
        "node_a": make_recording_node("node_a"),
        "node_b": make_recording_node("node_b"),
        "node_c": make_recording_node("node_c"),
    })
    engine = OrchestrationEngine(scenario=scenario, checkpointer=MemorySaver())
    final_state = engine.run(_make_state())

    assert execution_order == ["node_a", "node_b", "node_c"]


def test_engine_state_accumulates_across_nodes():
    MinimalScenario = _make_minimal_scenario()
    nodes = _make_nodes(["node_a", "node_b", "node_c"])
    scenario = MinimalScenario(nodes)
    engine = OrchestrationEngine(scenario=scenario, checkpointer=MemorySaver())
    final_state = engine.run(_make_state())

    artifacts = final_state.get("stage_artifacts", {})
    assert "node_a" in artifacts
    assert "node_b" in artifacts
    assert "node_c" in artifacts


def test_engine_uses_run_id_as_thread_id():
    """Verify two runs with different run_ids don't share state."""
    MinimalScenario = _make_minimal_scenario()
    saver = MemorySaver()

    nodes_a = _make_nodes(["node_a", "node_b", "node_c"])
    engine = OrchestrationEngine(scenario=MinimalScenario(nodes_a), checkpointer=saver)

    state1 = _make_state(run_id="run-001", requirement="req A")
    state2 = _make_state(run_id="run-002", requirement="req B")

    result1 = engine.run(state1)
    result2 = engine.run(state2)

    assert result1["run_id"] == "run-001"
    assert result2["run_id"] == "run-002"


def test_engine_get_state_returns_snapshot():
    MinimalScenario = _make_minimal_scenario()
    saver = MemorySaver()
    engine = OrchestrationEngine(scenario=MinimalScenario(_make_nodes(["node_a", "node_b", "node_c"])), checkpointer=saver)
    engine.run(_make_state(run_id="run-snap"))
    snapshot = engine.get_state("run-snap")
    assert snapshot is not None


def test_engine_graph_is_cached_across_calls():
    MinimalScenario = _make_minimal_scenario()
    engine = OrchestrationEngine(
        scenario=MinimalScenario(_make_nodes(["node_a", "node_b", "node_c"])),
        checkpointer=MemorySaver(),
    )
    g1 = engine._get_graph()
    g2 = engine._get_graph()
    assert g1 is g2   # same compiled graph object


# ── GreenFieldScenario: conditional routing ───────────────────────────────────


def test_greenfield_routes_to_schema_gate_when_schema_change_detected():
    """When schema_change_detected=True, implementation routes to schema_gate."""
    visited = []

    def make_node(name, extra_state=None):
        def node(state):
            visited.append(name)
            update = {"stage_artifacts": {**state.get("stage_artifacts", {}), name: True}}
            if extra_state:
                update.update(extra_state)
            return update
        node.__name__ = name
        return node

    nodes = {name: make_node(name) for name in GreenFieldScenario.node_names()}
    # Override implementation to set schema_change_detected=True
    nodes["implementation"] = make_node("implementation", {"schema_change_detected": True})

    scenario = GreenFieldScenario(nodes)
    engine = OrchestrationEngine(scenario=scenario, checkpointer=MemorySaver())
    engine.run(_make_state(schema_change_detected=False))

    assert "schema_gate" in visited


def test_greenfield_skips_schema_gate_when_no_schema_change():
    """When schema_change_detected=False, implementation routes directly to unit_tests."""
    visited = []

    def make_node(name):
        def node(state):
            visited.append(name)
            return {"stage_artifacts": {**state.get("stage_artifacts", {}), name: True}}
        node.__name__ = name
        return node

    nodes = {name: make_node(name) for name in GreenFieldScenario.node_names()}
    scenario = GreenFieldScenario(nodes)
    engine = OrchestrationEngine(scenario=scenario, checkpointer=MemorySaver())
    engine.run(_make_state(schema_change_detected=False))

    assert "schema_gate" not in visited
    assert "unit_tests" in visited


# ── Scenario variants ─────────────────────────────────────────────────────────


def test_brownfield_builds_same_topology():
    nodes = _make_nodes(GreenFieldScenario.node_names())
    scenario = BrownfieldScenario(nodes)
    graph = scenario.build_graph(MemorySaver())
    assert graph is not None


def test_ambiguous_builds_same_topology():
    nodes = _make_nodes(GreenFieldScenario.node_names())
    scenario = AmbiguousScenario(nodes)
    graph = scenario.build_graph(MemorySaver())
    assert graph is not None


def test_all_three_scenarios_run_to_completion():
    nodes = _make_nodes(GreenFieldScenario.node_names())
    saver = MemorySaver()
    for ScenarioCls, scenario_type in [
        (GreenFieldScenario, "greenfield"),
        (BrownfieldScenario, "brownfield"),
        (AmbiguousScenario, "ambiguous"),
    ]:
        scenario = ScenarioCls({n: _counter_node(n) for n in GreenFieldScenario.node_names()})
        engine = OrchestrationEngine(scenario=scenario, checkpointer=MemorySaver())
        state = _make_state(
            run_id=f"run-{scenario_type}",
            scenario_type=scenario_type,
        )
        final = engine.run(state)
        artifacts = final.get("stage_artifacts", {})
        assert "release_gate" in artifacts, f"{scenario_type} did not complete"

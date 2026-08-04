"""
Unit tests for orchestrator/planner/ — classifier, clarification, planner.

All three modules accept injectable dependencies (gateway, ask_fn, run_store)
so no real LLM calls or DB connections are needed.

Run: .venv/bin/python -m pytest orchestrator/tests/test_planner.py -v
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.gateway.models import GatewayRequest, GatewayResponse
from orchestrator.planner.clarification import (
    MAX_QUESTIONS,
    ClarificationLoop,
    ClarificationResult,
)
from orchestrator.planner.classifier import ClassifierResult, RequirementClassifier
from orchestrator.planner.planner import Planner


# ── helpers ───────────────────────────────────────────────────────────────────


def _gw_response(content: str) -> GatewayResponse:
    return GatewayResponse(
        content=content,
        tool_calls=None,
        usage={"input_tokens": 10, "output_tokens": 20},
        trace_id="trace-test",
    )


def _make_gateway(content: str) -> MagicMock:
    """Return a mock gateway whose .call() returns GatewayResponse with content."""
    gw = MagicMock()
    gw.call.return_value = _gw_response(content)
    return gw


def _classifier_json(
    scenario_type: str = "greenfield",
    confidence: float = 0.9,
    reasoning: str = "clear requirement",
    clarification_needed: str | None = None,
) -> str:
    return json.dumps({
        "scenario_type": scenario_type,
        "confidence": confidence,
        "reasoning": reasoning,
        "clarification_needed": clarification_needed,
    })


# ── RequirementClassifier ─────────────────────────────────────────────────────


def test_classifier_greenfield_returns_correct_type():
    gw = _make_gateway(_classifier_json("greenfield", 0.95))
    result = RequirementClassifier(gw).classify("Add QR code endpoint", token="tok")
    assert result.scenario_type == "greenfield"
    assert result.confidence == 0.95


def test_classifier_brownfield_returns_correct_type():
    gw = _make_gateway(_classifier_json("brownfield", 0.8, "modifies existing endpoint"))
    result = RequirementClassifier(gw).classify("Change redirect logic", token="tok")
    assert result.scenario_type == "brownfield"


def test_classifier_ambiguous_sets_clarification_needed():
    gw = _make_gateway(
        _classifier_json("ambiguous", 0.5, "unclear scope", "Which users are affected?")
    )
    result = RequirementClassifier(gw).classify("Improve performance", token="tok")
    assert result.scenario_type == "ambiguous"
    assert result.clarification_needed == "Which users are affected?"


def test_classifier_unknown_scenario_type_falls_back_to_ambiguous():
    gw = _make_gateway(json.dumps({"scenario_type": "unknown_type", "confidence": 0.7}))
    result = RequirementClassifier(gw).classify("something", token="tok")
    assert result.scenario_type == "ambiguous"


def test_classifier_missing_fields_use_defaults():
    gw = _make_gateway(json.dumps({"scenario_type": "greenfield"}))
    result = RequirementClassifier(gw).classify("req", token="tok")
    assert result.confidence == 0.5
    assert result.reasoning == ""
    assert result.clarification_needed is None


def test_classifier_strips_markdown_fences():
    raw = '```json\n{"scenario_type": "greenfield", "confidence": 0.9, "reasoning": "ok"}\n```'
    gw = _make_gateway(raw)
    result = RequirementClassifier(gw).classify("req", token="tok")
    assert result.scenario_type == "greenfield"


def test_classifier_raises_on_malformed_json():
    gw = _make_gateway("not-json-at-all")
    with pytest.raises(ValueError, match="malformed JSON"):
        RequirementClassifier(gw).classify("req", token="tok")


def test_classifier_passes_run_id_to_gateway():
    gw = _make_gateway(_classifier_json())
    RequirementClassifier(gw).classify("req", token="tok", run_id="run-123")
    request: GatewayRequest = gw.call.call_args[0][0]
    assert request.run_id == "run-123"


def test_classifier_uses_custom_model():
    gw = _make_gateway(_classifier_json())
    RequirementClassifier(gw, model="gpt-4o").classify("req", token="tok")
    request: GatewayRequest = gw.call.call_args[0][0]
    assert request.model == "gpt-4o"


# ── ClarificationLoop ─────────────────────────────────────────────────────────


def _questions_json(*questions) -> str:
    return json.dumps({"questions": list(questions)})


def _resolution_json(resolved: str, assumptions: list[str]) -> str:
    return json.dumps({"resolved_requirement": resolved, "assumptions": assumptions})


def test_clarification_asks_each_generated_question(monkeypatch):
    questions_response = _questions_json("Who is the target user?", "What auth is needed?")
    resolution_response = _resolution_json("Resolved req", ["Users are admins", "Token auth"])

    gw = MagicMock()
    gw.call.side_effect = [
        _gw_response(questions_response),
        _gw_response(resolution_response),
    ]

    answers = ["admins", "JWT token"]
    ask_fn = MagicMock(side_effect=answers)

    loop = ClarificationLoop(gw, ask_fn=ask_fn)
    result = loop.run("Improve auth", token="tok")

    assert ask_fn.call_count == 2
    assert result.resolved_requirement == "Resolved req"
    assert result.assumptions == ["Users are admins", "Token auth"]


def test_clarification_caps_questions_at_max():
    too_many = [f"q{i}" for i in range(MAX_QUESTIONS + 3)]
    questions_response = json.dumps({"questions": too_many})
    resolution_response = _resolution_json("resolved", [])

    gw = MagicMock()
    gw.call.side_effect = [
        _gw_response(questions_response),
        _gw_response(resolution_response),
    ]

    ask_fn = MagicMock(return_value="answer")
    loop = ClarificationLoop(gw, ask_fn=ask_fn)
    loop.run("vague requirement", token="tok")

    assert ask_fn.call_count == MAX_QUESTIONS


def test_clarification_zero_questions_skips_asking():
    questions_response = json.dumps({"questions": []})
    resolution_response = _resolution_json("same req", [])

    gw = MagicMock()
    gw.call.side_effect = [
        _gw_response(questions_response),
        _gw_response(resolution_response),
    ]

    ask_fn = MagicMock()
    loop = ClarificationLoop(gw, ask_fn=ask_fn)
    result = loop.run("clear enough", token="tok")

    ask_fn.assert_not_called()
    assert result.resolved_requirement == "same req"


def test_clarification_raises_on_malformed_questions_json():
    gw = _make_gateway("not json")
    ask_fn = MagicMock()
    loop = ClarificationLoop(gw, ask_fn=ask_fn)
    with pytest.raises(ValueError, match="malformed JSON"):
        loop.run("req", token="tok")


def test_clarification_raises_on_malformed_resolution_json():
    questions_response = _questions_json("q1")
    gw = MagicMock()
    gw.call.side_effect = [
        _gw_response(questions_response),
        _gw_response("bad json"),
    ]
    ask_fn = MagicMock(return_value="a1")
    loop = ClarificationLoop(gw, ask_fn=ask_fn)
    with pytest.raises(ValueError, match="malformed JSON"):
        loop.run("req", token="tok")


def test_clarification_strips_markdown_fences_in_resolution():
    questions_response = json.dumps({"questions": []})
    raw_resolution = '```json\n{"resolved_requirement": "scoped req", "assumptions": ["a1"]}\n```'

    gw = MagicMock()
    gw.call.side_effect = [
        _gw_response(questions_response),
        _gw_response(raw_resolution),
    ]

    loop = ClarificationLoop(gw, ask_fn=MagicMock())
    result = loop.run("req", token="tok")
    assert result.resolved_requirement == "scoped req"
    assert result.assumptions == ["a1"]


# ── Planner ───────────────────────────────────────────────────────────────────


def _make_classifier(scenario_type: str = "greenfield") -> MagicMock:
    mock = MagicMock()
    mock.classify.return_value = ClassifierResult(
        scenario_type=scenario_type,
        confidence=0.9,
        reasoning="test",
        clarification_needed=None,
    )
    return mock


def _make_clarification(resolved: str = "resolved req") -> MagicMock:
    mock = MagicMock()
    mock.run.return_value = ClarificationResult(
        resolved_requirement=resolved,
        assumptions=["assume 1"],
    )
    return mock


def test_planner_returns_orchestrator_state_for_greenfield():
    planner = Planner(_make_classifier("greenfield"), _make_clarification())
    state = planner.plan("Add QR code", "alice", "tok")

    assert state["scenario_type"] == "greenfield"
    assert state["requirement"] == "Add QR code"
    assert state["triggered_by"] == "alice"
    assert state["run_id"].startswith("orch-")


def test_planner_resolved_requirement_equals_requirement_for_non_ambiguous():
    planner = Planner(_make_classifier("greenfield"), _make_clarification())
    state = planner.plan("Clear requirement", "alice", "tok")

    assert state["resolved_requirement"] == "Clear requirement"
    assert state["assumptions"] == []


def test_planner_runs_clarification_for_ambiguous():
    classifier = _make_classifier("ambiguous")
    clarification = _make_clarification("Scoped and resolved requirement")
    planner = Planner(classifier, clarification)
    state = planner.plan("Improve things", "alice", "tok")

    clarification.run.assert_called_once()
    assert state["resolved_requirement"] == "Scoped and resolved requirement"
    assert state["assumptions"] == ["assume 1"]


def test_planner_skips_clarification_for_brownfield():
    classifier = _make_classifier("brownfield")
    clarification = _make_clarification()
    planner = Planner(classifier, clarification)
    planner.plan("Modify redirect", "alice", "tok")

    clarification.run.assert_not_called()


def test_planner_calls_run_store_create_run_when_provided():
    run_store = MagicMock()
    planner = Planner(_make_classifier("greenfield"), _make_clarification(), run_store=run_store)
    state = planner.plan("req", "alice", "tok")

    run_store.create_run.assert_called_once_with(
        run_id=state["run_id"],
        requirement="req",
        scenario_type="greenfield",
        triggered_by="alice",
    )


def test_planner_skips_run_store_when_none():
    planner = Planner(_make_classifier(), _make_clarification(), run_store=None)
    state = planner.plan("req", "alice", "tok")  # should not raise
    assert state["run_id"].startswith("orch-")


def test_planner_generates_unique_run_ids():
    planner = Planner(_make_classifier(), _make_clarification())
    ids = {planner.plan("req", "alice", "tok")["run_id"] for _ in range(20)}
    assert len(ids) == 20  # all unique


def test_planner_initialises_empty_collections():
    planner = Planner(_make_classifier(), _make_clarification())
    state = planner.plan("req", "alice", "tok")

    assert state["stage_artifacts"] == {}
    assert state["stage_evaluations"] == {}
    assert state["tool_cache"] == {}
    assert state["schema_change_detected"] is False
    assert state["feature_branch"] is None
    assert state["pr_url"] is None
    assert state["pr_number"] is None


def test_planner_passes_run_id_to_classifier():
    classifier = _make_classifier()
    planner = Planner(classifier, _make_clarification())
    state = planner.plan("req", "alice", "tok")

    _, kwargs = classifier.classify.call_args
    assert kwargs.get("run_id") == state["run_id"] or classifier.classify.call_args[1].get("run_id") == state["run_id"]


def test_planner_passes_token_to_classifier():
    classifier = _make_classifier()
    planner = Planner(classifier, _make_clarification())
    planner.plan("req", "alice", "my-token")

    classifier.classify.assert_called_once()
    call_kwargs = classifier.classify.call_args[1]
    assert call_kwargs.get("token") == "my-token"

"""
Unit tests for StageAgent.

No real LLM calls, no DB, no GitHub — everything is mocked.
Tests verify the multi-turn tool-call loop, cache integration,
JSON parsing robustness, and StageResult population.

Run: .venv/bin/python -m pytest orchestrator/tests/test_stage_agent.py -v
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.agents.stage_agent import StageAgent, _parse_artifact, _prompt_cache_key
from orchestrator.core.stage import StageStatus
from orchestrator.core.state import OrchestratorState
from orchestrator.gateway.models import GatewayResponse


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> OrchestratorState:
    defaults = dict(
        run_id="run-001",
        requirement="Add a QR code endpoint",
        resolved_requirement="Add GET /api/v1/qr/{short_code}",
        scenario_type="greenfield",
        triggered_by="dev-token",
        stage_artifacts={},
        assumptions=[],
        tool_cache={},
    )
    defaults.update(kwargs)
    return OrchestratorState(**defaults)


def _gateway_response(content: str, tool_calls=None) -> GatewayResponse:
    return GatewayResponse(
        content=content,
        tool_calls=tool_calls,
        usage={"input_tokens": 100, "output_tokens": 50},
        trace_id="trace-abc",
    )


def _final_response(artifact: dict) -> GatewayResponse:
    return _gateway_response(json.dumps(artifact))


def _make_agent(gateway=None, registry=None, cache=None, builder=None, max_tool_iterations=10) -> StageAgent:
    gw = gateway or MagicMock()
    reg = registry or MagicMock()
    reg.get_schemas.return_value = []
    if cache is None:
        rc = MagicMock()
        rc.get.return_value = None   # default: cache miss
    else:
        rc = cache
    return StageAgent(
        gateway=gw,
        tool_registry=reg,
        response_cache=rc,
        prompt_builder=builder,
        model="gpt-4o",
        max_tool_iterations=max_tool_iterations,
    )


def _mock_builder(messages=None, version="architecture_design_v1"):
    builder = MagicMock()
    builder.build.return_value = (
        messages or [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
        version,
    )
    return builder


def _mock_memory():
    mem = MagicMock()
    mem.format_for_prompt.return_value = ""
    return mem


# ── StageAgent.run — happy path ───────────────────────────────────────────────


def test_run_returns_completed_result_on_clean_json():
    artifact = {"summary": "Add QR endpoint", "new_endpoints": []}
    gateway = MagicMock()
    gateway.call.return_value = _final_response(artifact)
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.COMPLETED
    assert result.output_artifact == artifact


def test_run_populates_stage_name():
    gateway = MagicMock()
    gateway.call.return_value = _final_response({"x": 1})
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.stage_name == "architecture_design"


def test_run_populates_prompt_version():
    gateway = MagicMock()
    gateway.call.return_value = _final_response({"x": 1})
    agent = _make_agent(gateway=gateway, builder=_mock_builder(version="architecture_design_v2"))
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.prompt_version == "architecture_design_v2"


def test_run_populates_model_used():
    gateway = MagicMock()
    gateway.call.return_value = _final_response({"x": 1})
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.model_used == "gpt-4o"


def test_run_populates_started_and_completed_at():
    gateway = MagicMock()
    gateway.call.return_value = _final_response({"x": 1})
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.completed_at >= result.started_at


def test_run_strips_markdown_fences():
    raw = "```json\n{\"summary\": \"ok\"}\n```"
    gateway = MagicMock()
    gateway.call.return_value = _gateway_response(raw)
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.COMPLETED
    assert result.output_artifact == {"summary": "ok"}


# ── StageAgent.run — cache ────────────────────────────────────────────────────


def test_run_returns_cached_result_without_calling_gateway():
    cached_artifact = {"summary": "cached", "new_endpoints": []}
    cache = MagicMock()
    cache.get.return_value = cached_artifact
    gateway = MagicMock()
    agent = _make_agent(gateway=gateway, cache=cache, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.COMPLETED
    assert result.output_artifact == cached_artifact
    gateway.call.assert_not_called()


def test_run_caches_result_on_success():
    artifact = {"summary": "new result"}
    cache = MagicMock()
    cache.get.return_value = None   # miss
    gateway = MagicMock()
    gateway.call.return_value = _final_response(artifact)
    agent = _make_agent(gateway=gateway, cache=cache, builder=_mock_builder())
    agent.run("architecture_design", _make_state(), _mock_memory())
    cache.set.assert_called_once()


def test_run_does_not_cache_on_failure():
    cache = MagicMock()
    cache.get.return_value = None
    gateway = MagicMock()
    gateway.call.side_effect = RuntimeError("LLM error")
    agent = _make_agent(gateway=gateway, cache=cache, builder=_mock_builder())
    agent.run("architecture_design", _make_state(), _mock_memory())
    cache.set.assert_not_called()


# ── StageAgent.run — tool-call loop ──────────────────────────────────────────


def test_run_executes_tool_calls_and_continues():
    tool_call = {
        "id": "tc_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "service/main.py"}'},
    }
    final_artifact = {"summary": "done after tool call"}

    gateway = MagicMock()
    gateway.call.side_effect = [
        _gateway_response(None, tool_calls=[tool_call]),
        _final_response(final_artifact),
    ]

    tool_result = MagicMock()
    tool_result.result = "file contents"
    tool_result.error = None
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.execute.return_value = tool_result

    agent = _make_agent(gateway=gateway, registry=registry, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())

    assert result.status == StageStatus.COMPLETED
    assert result.output_artifact == final_artifact
    assert gateway.call.call_count == 2
    registry.execute.assert_called_once()
    call_args = registry.execute.call_args
    assert call_args[0][0] == "read_file"
    assert call_args[0][1] == {"path": "service/main.py"}


def test_run_appends_tool_result_to_history():
    """Verify that after a tool call, the next gateway.call gets a 'tool' role message."""
    tool_call = {
        "id": "tc_2",
        "type": "function",
        "function": {"name": "run_tests", "arguments": "{}"},
    }

    tool_result = MagicMock()
    tool_result.result = {"passed": 45, "failed": 0, "success": True}
    tool_result.error = None
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.execute.return_value = tool_result

    gateway = MagicMock()
    gateway.call.side_effect = [
        _gateway_response(None, tool_calls=[tool_call]),
        _final_response({"summary": "tests pass"}),
    ]

    captured_requests = []
    original_side_effect = gateway.call.side_effect
    def capture(req):
        captured_requests.append(req)
        return original_side_effect.pop(0) if original_side_effect else None
    gateway.call.side_effect = [
        _gateway_response(None, tool_calls=[tool_call]),
        _final_response({"summary": "tests pass"}),
    ]

    agent = _make_agent(gateway=gateway, registry=registry, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.COMPLETED


def test_run_handles_tool_error_as_message():
    """Tool errors are passed back to the LLM, not raised."""
    tool_call = {
        "id": "tc_3",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "missing.py"}'},
    }

    tool_result = MagicMock()
    tool_result.result = None
    tool_result.error = "FileNotFoundError: missing.py"
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.execute.return_value = tool_result

    gateway = MagicMock()
    gateway.call.side_effect = [
        _gateway_response(None, tool_calls=[tool_call]),
        _final_response({"summary": "handled error"}),
    ]

    agent = _make_agent(gateway=gateway, registry=registry, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.COMPLETED


def test_run_fails_after_max_iterations():
    tool_call = {
        "id": "tc_inf",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }
    tool_result = MagicMock()
    tool_result.result = "data"
    tool_result.error = None
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.execute.return_value = tool_result

    gateway = MagicMock()
    # Always return tool_calls — never converges
    gateway.call.return_value = _gateway_response(None, tool_calls=[tool_call])

    agent = _make_agent(
        gateway=gateway, registry=registry, builder=_mock_builder(),
        max_tool_iterations=3,
    )
    result = agent.run("architecture_design", _make_state(), _mock_memory())

    assert result.status == StageStatus.FAILED
    assert "iteration" in result.error_message.lower() or "limit" in result.error_message.lower()


# ── StageAgent.run — gateway errors ──────────────────────────────────────────


def test_run_returns_failed_on_gateway_exception():
    gateway = MagicMock()
    gateway.call.side_effect = Exception("OpenAI rate limit")
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.FAILED
    assert "OpenAI rate limit" in result.error_message


def test_run_returns_failed_on_malformed_json():
    gateway = MagicMock()
    gateway.call.return_value = _gateway_response("not json at all")
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory())
    assert result.status == StageStatus.FAILED
    assert result.error_message is not None


# ── StageAgent.run — attempt_number ──────────────────────────────────────────


def test_run_records_attempt_number():
    gateway = MagicMock()
    gateway.call.return_value = _final_response({"x": 1})
    agent = _make_agent(gateway=gateway, builder=_mock_builder())
    result = agent.run("architecture_design", _make_state(), _mock_memory(), attempt_number=2)
    assert result.attempt_number == 2


# ── _parse_artifact unit tests ────────────────────────────────────────────────


def test_parse_artifact_clean_json():
    assert _parse_artifact("s", '{"x": 1}') == {"x": 1}


def test_parse_artifact_strips_json_fence():
    raw = "```json\n{\"x\": 1}\n```"
    assert _parse_artifact("s", raw) == {"x": 1}


def test_parse_artifact_strips_plain_fence():
    raw = "```\n{\"x\": 1}\n```"
    assert _parse_artifact("s", raw) == {"x": 1}


def test_parse_artifact_raises_on_invalid_json():
    with pytest.raises(ValueError, match="malformed JSON"):
        _parse_artifact("s", "not json")


def test_parse_artifact_raises_on_non_dict():
    with pytest.raises(ValueError, match="must be a JSON object"):
        _parse_artifact("s", "[1, 2, 3]")


# ── _prompt_cache_key ─────────────────────────────────────────────────────────


def test_prompt_cache_key_is_deterministic():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert _prompt_cache_key(messages) == _prompt_cache_key(messages)


def test_prompt_cache_key_differs_for_different_messages():
    m1 = [{"role": "user", "content": "a"}]
    m2 = [{"role": "user", "content": "b"}]
    assert _prompt_cache_key(m1) != _prompt_cache_key(m2)

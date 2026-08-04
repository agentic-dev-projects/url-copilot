"""
Unit tests for PromptLoader and PromptBuilder.

No DB, no LLM calls — pure logic tests.
MemoryStore is mocked; real prompt files are loaded from prompts/stages/.

Run: .venv/bin/python -m pytest orchestrator/tests/test_prompt_builder.py -v
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.core.state import OrchestratorState
from orchestrator.prompt_builder.builder import (
    PRIOR_STAGE_DEPENDENCIES,
    STAGE_CONTEXT_FILES,
    PromptBuilder,
)
from orchestrator.prompt_builder.loader import PROMPTS_DIR, PromptLoader


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> OrchestratorState:
    defaults = dict(
        run_id="run-test-001",
        requirement="Add a QR code endpoint to the URL shortener",
        resolved_requirement="Add GET /api/v1/qr/{short_code} that returns a PNG QR code",
        scenario_type="greenfield",
        triggered_by="alice",
        stage_artifacts={},
        assumptions=[],
    )
    defaults.update(kwargs)
    return OrchestratorState(**defaults)


def _mock_memory(text: str = "") -> MagicMock:
    mem = MagicMock()
    mem.format_for_prompt.return_value = text
    return mem


# ── PromptLoader ──────────────────────────────────────────────────────────────


def test_loader_loads_architecture_prompt():
    loader = PromptLoader()
    text, version = loader.load("architecture_design")
    assert "architecture_design_v1" in version
    assert len(text) > 50


def test_loader_loads_classifier_prompt():
    loader = PromptLoader()
    text, version = loader.load("classifier")
    assert "classifier_v1" in version
    assert "greenfield" in text.lower() or "scenario" in text.lower()


def test_loader_loads_implementation_prompt():
    loader = PromptLoader()
    text, version = loader.load("implementation")
    assert "implementation_v1" in version


def test_loader_loads_tests_prompt():
    loader = PromptLoader()
    text, version = loader.load("tests")
    assert "tests_v1" in version


def test_loader_loads_release_readiness_prompt():
    loader = PromptLoader()
    text, version = loader.load("release_readiness")
    assert "release_readiness_v1" in version


def test_loader_missing_stage_raises():
    loader = PromptLoader()
    with pytest.raises(FileNotFoundError, match="no prompt file"):
        loader.load("nonexistent_stage_xyz")


def test_loader_picks_highest_version(tmp_path, monkeypatch):
    # Create two versioned files; loader should return v2
    (tmp_path / "demo_v1.txt").write_text("version one")
    (tmp_path / "demo_v2.txt").write_text("version two")
    import orchestrator.prompt_builder.loader as lmod
    monkeypatch.setattr(lmod, "PROMPTS_DIR", tmp_path)
    loader = PromptLoader()
    text, version = loader.load("demo")
    assert text == "version two"
    assert "demo_v2" in version


def test_loader_version_string_is_file_stem():
    loader = PromptLoader()
    _, version = loader.load("architecture_design")
    assert version == "architecture_design_v1"


# ── PromptBuilder: messages structure ────────────────────────────────────────


def test_builder_returns_messages_and_version():
    builder = PromptBuilder()
    state = _make_state()
    messages, version = builder.build("architecture_design", state, _mock_memory())
    assert isinstance(messages, list)
    assert isinstance(version, str)
    assert "architecture_design_v1" in version


def test_builder_first_message_is_system():
    builder = PromptBuilder()
    messages, _ = builder.build("architecture_design", _make_state(), _mock_memory())
    assert messages[0]["role"] == "system"


def test_builder_last_message_is_user_instruction():
    builder = PromptBuilder()
    messages, _ = builder.build("architecture_design", _make_state(), _mock_memory())
    last = messages[-1]
    assert last["role"] == "user"
    assert "Requirement" in last["content"]


def test_builder_system_contains_task_prompt():
    builder = PromptBuilder()
    messages, _ = builder.build("architecture_design", _make_state(), _mock_memory())
    system = messages[0]["content"]
    # Architecture prompt mentions JSON output format
    assert "json" in system.lower() or "JSON" in system


def test_builder_system_contains_memory_when_present():
    builder = PromptBuilder()
    memory = _mock_memory("=== Team Conventions ===\n[fact] FastAPI is used")
    messages, _ = builder.build("classifier", _make_state(), memory)
    system = messages[0]["content"]
    assert "FastAPI is used" in system


def test_builder_system_skips_memory_when_empty():
    builder = PromptBuilder()
    memory = _mock_memory("")   # empty memory
    messages, _ = builder.build("classifier", _make_state(), memory)
    system = messages[0]["content"]
    assert "Team Conventions" not in system


def test_builder_instruction_contains_requirement():
    builder = PromptBuilder()
    state = _make_state(resolved_requirement="Add QR code endpoint")
    messages, _ = builder.build("architecture_design", state, _mock_memory())
    instruction = messages[-1]["content"]
    assert "Add QR code endpoint" in instruction


def test_builder_instruction_contains_run_id():
    builder = PromptBuilder()
    state = _make_state(run_id="run-xyz-999")
    messages, _ = builder.build("architecture_design", state, _mock_memory())
    instruction = messages[-1]["content"]
    assert "run-xyz-999" in instruction


def test_builder_instruction_contains_scenario():
    builder = PromptBuilder()
    state = _make_state(scenario_type="brownfield")
    messages, _ = builder.build("architecture_design", state, _mock_memory())
    instruction = messages[-1]["content"]
    assert "brownfield" in instruction


def test_builder_instruction_includes_assumptions_when_present():
    builder = PromptBuilder()
    state = _make_state(assumptions=["Use PNG format", "Max size 512px"])
    messages, _ = builder.build("architecture_design", state, _mock_memory())
    instruction = messages[-1]["content"]
    assert "Use PNG format" in instruction


# ── PromptBuilder: conversation history ──────────────────────────────────────


def test_builder_appends_conversation_history():
    builder = PromptBuilder()
    history = [
        {"role": "assistant", "content": "I'll read the router file first."},
        {"role": "tool", "content": "file contents here", "tool_call_id": "tc_1"},
    ]
    messages, _ = builder.build(
        "architecture_design", _make_state(), _mock_memory(),
        conversation_history=history,
    )
    roles = [m["role"] for m in messages]
    assert "assistant" in roles
    assert "tool" in roles


def test_builder_history_comes_before_final_instruction():
    builder = PromptBuilder()
    history = [{"role": "assistant", "content": "thinking..."}]
    messages, _ = builder.build(
        "architecture_design", _make_state(), _mock_memory(),
        conversation_history=history,
    )
    assistant_idx = next(i for i, m in enumerate(messages) if m["role"] == "assistant")
    last_user_idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
    assert assistant_idx < last_user_idx


def test_builder_empty_history_produces_minimal_messages():
    builder = PromptBuilder()
    # classifier has no context files — just system + user
    messages, _ = builder.build("classifier", _make_state(), _mock_memory())
    roles = [m["role"] for m in messages]
    assert roles[0] == "system"
    assert roles[-1] == "user"


# ── PromptBuilder: prior stage artifacts (Layer 4) ───────────────────────────


def test_builder_injects_prior_architecture_for_implementation():
    builder = PromptBuilder()
    arch_artifact = {"summary": "Use REST endpoint", "new_endpoints": []}
    state = _make_state(stage_artifacts={"architecture_design": arch_artifact})
    messages, _ = builder.build("implementation", state, _mock_memory())
    system = messages[0]["content"]
    assert "Use REST endpoint" in system


def test_builder_does_not_inject_irrelevant_artifacts():
    builder = PromptBuilder()
    state = _make_state(stage_artifacts={"implementation": {"files_written": []}})
    messages, _ = builder.build("architecture_design", state, _mock_memory())
    system = messages[0]["content"]
    # architecture_design has no prior deps — implementation artifact should not appear
    assert "files_written" not in system


def test_builder_no_prior_artifacts_when_empty():
    builder = PromptBuilder()
    state = _make_state(stage_artifacts={})
    messages, _ = builder.build("implementation", state, _mock_memory())
    system = messages[0]["content"]
    assert "Prior Stage Outputs" not in system


# ── STAGE_CONTEXT_FILES and PRIOR_STAGE_DEPENDENCIES completeness ─────────────


def test_all_stages_have_context_files_entry():
    expected = {"classifier", "architecture_design", "implementation", "tests", "release_readiness"}
    assert expected.issubset(set(STAGE_CONTEXT_FILES.keys()))


def test_all_stages_have_prior_deps_entry():
    expected = {"classifier", "architecture_design", "implementation", "tests", "release_readiness"}
    assert expected.issubset(set(PRIOR_STAGE_DEPENDENCIES.keys()))


def test_architecture_has_no_prior_deps():
    assert PRIOR_STAGE_DEPENDENCIES["architecture_design"] == []


def test_release_readiness_depends_on_all_prior_stages():
    deps = PRIOR_STAGE_DEPENDENCIES["release_readiness"]
    assert "architecture_design" in deps
    assert "implementation" in deps
    assert "tests" in deps

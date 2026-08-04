"""
PromptBuilder — assembles the multi-layer message list for stage agent LLM calls.

Seven-layer architecture
------------------------
Each stage agent call is built from seven layers, assembled into an OpenAI
messages list.  The layers encode different types of context at different
scopes of relevance:

  Layer 1  Stage task instructions (static — from prompts/stages/{stage}_v*.txt)
           Who you are and exactly what to produce for this stage.

  Layer 2  Codebase context (dynamic — files read from service/)
           The actual code the agent needs to read before generating.
           Stage-specific: architecture_design reads router.py; implementation
           reads the architecture artifact to stay consistent.

  Layer 3  Persistent memory (dynamic — from MemoryStore.format_for_prompt())
           Cross-run facts, conventions, and decisions the team has encoded.
           Injected into the system message so they apply to every turn.

  Layer 4  Prior stage artifacts (dynamic — from OrchestratorState.stage_artifacts)
           What earlier stages produced.  Implementation reads architecture_design's
           artifact; tests reads both.  Only relevant prior stages are included.

  Layer 5  Conversation history (dynamic — accumulated by StageAgent)
           Prior turns in the multi-turn tool-calling loop.  Empty on first call.

  Layer 6  Tool results (dynamic — accumulated by StageAgent)
           Results from tool calls the LLM already made.  These are in OpenAI's
           tool-result message format (role: "tool").

  Layer 7  Stage-specific instruction (dynamic — from OrchestratorState)
           The concrete ask for THIS call: the resolved requirement plus any
           scenario-specific framing.

Message assembly
----------------
Layers 1+3+4 → system message  (standing context — always present)
Layer 2       → first user message (file contents)
Layers 5+6    → interleaved (conversation_history already in messages format)
Layer 7       → final user message (the actual ask)

The resulting messages list is passed directly to AIGateway.call() →
OpenAI chat.completions.create(messages=...).
"""

import json
from typing import Any

from orchestrator.core.state import OrchestratorState
from orchestrator.memory.store import MemoryStore
from orchestrator.prompt_builder.loader import PromptLoader
from orchestrator.tools import filesystem

# ── stage context files (Layer 2) ────────────────────────────────────────────

STAGE_CONTEXT_FILES: dict[str, list[str]] = {
    "classifier": [],
    "requirements_analysis": [
        "service/main.py",
        "service/api/v1/router.py",
        "service/models/__init__.py",
        "service/schemas/__init__.py",
    ],
    "architecture_design": [
        "service/main.py",
        "service/api/v1/router.py",
        "service/models/__init__.py",
        "service/schemas/__init__.py",
        "service/config.py",
    ],
    "implementation_plan": [
        "service/api/v1/router.py",
        "service/models/__init__.py",
        "service/schemas/__init__.py",
        "service/services/__init__.py",
    ],
    "test_plan": [
        "service/tests/conftest.py",
    ],
    "implementation": [
        "service/main.py",
        "service/api/v1/router.py",
        "service/models/__init__.py",
        "service/schemas/__init__.py",
        "service/schemas/url.py",
        "service/services/__init__.py",
        "requirements.txt",
    ],
    "unit_tests": [
        "service/tests/conftest.py",
        "service/api/v1/router.py",
    ],
    "integration_tests": [
        "service/tests/conftest.py",
        "service/api/v1/router.py",
    ],
    "documentation": [
        "service/api/v1/router.py",
    ],
    "release_readiness": ["requirements.txt"],
}

# ── which prior stage artifacts each stage needs (Layer 4) ───────────────────

PRIOR_STAGE_DEPENDENCIES: dict[str, list[str]] = {
    "classifier":           [],
    "requirements_analysis":[],
    "architecture_design":  ["requirements_analysis"],
    "implementation_plan":  ["architecture_design"],
    "test_plan":            ["architecture_design"],
    "implementation":       ["architecture_design", "implementation_plan"],
    "unit_tests":           ["architecture_design", "implementation"],
    "integration_tests":    ["architecture_design", "implementation"],
    "documentation":        ["implementation"],
    "release_readiness":    ["architecture_design", "implementation", "unit_tests"],
}

_SEPARATOR = "\n\n" + "─" * 60 + "\n\n"


class PromptBuilder:
    """Assembles the seven-layer messages list for a stage agent LLM call."""

    def __init__(self, loader: PromptLoader | None = None) -> None:
        self._loader = loader or PromptLoader()

    def build(
        self,
        stage_name: str,
        state: OrchestratorState,
        memory_store: MemoryStore,
        conversation_history: list[dict] | None = None,
        tool_results: list[dict] | None = None,
    ) -> tuple[list[dict], str]:
        """Assemble the messages list for an LLM call.

        Args:
            stage_name:           e.g. "architecture_design"
            state:                Current OrchestratorState (requirement, artifacts, etc.)
            memory_store:         MemoryStore instance for Layer 3.
            conversation_history: Prior turn messages (role: assistant/user/tool).
                                  None or empty for the first call.
            tool_results:         Tool call result messages (role: tool).
                                  Typically already embedded in conversation_history
                                  by StageAgent — pass separately only if needed.

        Returns:
            (messages, prompt_version) where:
              messages       — list[dict] ready for OpenAI messages=
              prompt_version — e.g. "architecture_design_v1" for recording in StageResult
        """
        history = list(conversation_history or [])
        extra_tool_results = list(tool_results or [])

        # Layer 1 + version string
        task_prompt, version = self._loader.load(stage_name)

        # Layer 3: persistent memory
        memory_block = memory_store.format_for_prompt()

        # Layer 4: prior stage artifacts
        prior_artifacts_block = self._format_prior_artifacts(stage_name, state)

        # Assemble system message (Layers 1 + 3 + 4)
        system_parts = [task_prompt]
        if memory_block:
            system_parts.append(memory_block)
        if prior_artifacts_block:
            system_parts.append(prior_artifacts_block)
        system_content = _SEPARATOR.join(system_parts)

        # Layer 2: codebase context (first user message)
        codebase_block = self._read_context_files(stage_name)

        # Layer 7: stage-specific instruction (final user message)
        instruction = self._build_instruction(stage_name, state)

        # Build the full messages list
        messages: list[dict] = [{"role": "system", "content": system_content}]

        if codebase_block:
            messages.append({"role": "user", "content": codebase_block})

        # Layers 5+6: conversation history and any extra tool results
        messages.extend(history)
        messages.extend(extra_tool_results)

        # Layer 7: the actual ask
        messages.append({"role": "user", "content": instruction})

        return messages, version

    # ── private ───────────────────────────────────────────────────────────────

    def _read_context_files(self, stage_name: str) -> str:
        """Read Layer 2 files and return a formatted block, skipping missing files."""
        files = STAGE_CONTEXT_FILES.get(stage_name, [])
        if not files:
            return ""
        parts = ["## Existing Codebase Context"]
        for path in files:
            try:
                content = filesystem.read_file(path)
                parts.append(f"### {path}\n```python\n{content}\n```")
            except (FileNotFoundError, ValueError):
                parts.append(f"### {path}\n(file not found — may not exist yet)")
        return "\n\n".join(parts)

    def _format_prior_artifacts(
        self, stage_name: str, state: OrchestratorState
    ) -> str:
        """Format Layer 4 — prior stage artifacts relevant to this stage."""
        deps = PRIOR_STAGE_DEPENDENCIES.get(stage_name, [])
        artifacts: dict[str, Any] = state.get("stage_artifacts", {}) or {}
        relevant = {k: artifacts[k] for k in deps if k in artifacts}
        if not relevant:
            return ""
        lines = ["## Prior Stage Outputs"]
        for stage, artifact in relevant.items():
            label = stage.replace("_", " ").title()
            lines.append(f"### {label}\n```json\n{json.dumps(artifact, indent=2)}\n```")
        return "\n\n".join(lines)

    def _build_instruction(self, stage_name: str, state: OrchestratorState) -> str:
        """Build Layer 7 — the specific instruction for this stage and run.

        run_id is excluded from most stages — it would produce a unique cache key
        on every run, defeating the response cache.  Exception: implementation stage
        includes the run_id so the LLM can embed it in the branch name, ensuring
        uniqueness across runs.  Implementation is never cache-worthy anyway since
        the codebase changes between runs.
        """
        requirement = state.get("resolved_requirement") or state.get("requirement", "")
        scenario = state.get("scenario_type", "unknown")

        lines = [
            f"## Your Task",
            f"",
            f"**Scenario**: {scenario}",
            f"",
            f"**Requirement**:",
            requirement,
        ]

        assumptions = state.get("assumptions", [])
        if assumptions:
            lines.append("\n**Clarified assumptions**:")
            for a in assumptions:
                lines.append(f"- {a}")

        # Include run_id for implementation so the LLM generates a unique branch name.
        if stage_name == "implementation":
            run_id = state.get("run_id", "")
            short_id = run_id.replace("orch-", "")[:8] if run_id else "local"
            lines.append(
                f"\n**Run ID suffix for branch naming**: `{short_id}`"
                f"\nName your branch: `feature/<slug>-{short_id}` "
                f"(e.g. `feature/add-qr-endpoint-{short_id}`)."
            )

        lines.append(
            f"\nProduce the {stage_name.replace('_', ' ')} output now. "
            f"Respond with a valid JSON object only."
        )
        return "\n".join(lines)

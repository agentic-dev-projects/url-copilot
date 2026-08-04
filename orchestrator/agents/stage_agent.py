"""
StageAgent — the execution unit for a single pipeline stage.

StageAgent orchestrates the full lifecycle of one LLM call sequence:
  1. Build the 7-layer prompt (PromptBuilder)
  2. Check the ResponseCache — return immediately on a hit
  3. Call AIGateway (enforces auth, rate limits, guardrails, cost tracking)
  4. If the LLM requests tool calls, execute them via ToolRegistry and loop
  5. When the LLM returns a final JSON response (no tool_calls), parse it
  6. Cache the response, return a StageResult

LangSmith tracing
-----------------
The @traceable decorator makes run() a named span in LangSmith.  Every
AIGateway.call() inside the loop automatically becomes a child span (via
wrap_openai in the gateway) — so the full trace tree is:

  stage_agent.run [span]
    gateway.call [span]
      OpenAI chat.completions [auto-traced]
    gateway.call [span]          ← second turn after tool calls
      OpenAI chat.completions [auto-traced]

No additional instrumentation is needed in the caller (OrchestrationEngine).

Multi-turn tool-call loop
--------------------------
OpenAI returns tool_calls when the LLM decides to call a tool instead of
producing a final answer.  Each tool call is executed via ToolRegistry (which
checks ToolCache first), then appended to the conversation as a "tool" role
message.  The updated conversation is sent back to the LLM until it returns
content with no tool_calls.

A MAX_TOOL_ITERATIONS guard prevents infinite loops — if the LLM keeps
emitting tool calls without converging, the run fails with a clear error.

JSON parsing
------------
Stage prompts instruct the LLM to respond with JSON only.  _parse_artifact()
strips markdown code fences (```json ... ```) before parsing, matching the
same robustness pattern used in ValidatorAgent.  Malformed JSON fails fast
with a clear error message rather than silently storing garbage in the artifact.

Error handling
--------------
Tool execution errors are caught and returned as tool result messages so the
LLM can decide how to handle them (retry a different path, report in output).
This mirrors how real coding agents work — the LLM should see errors and
adapt, not crash the whole run.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol

try:
    from langsmith import traceable
except ImportError:
    # langsmith not installed — @traceable becomes a no-op passthrough.
    # Install langsmith (see requirements.txt) to enable LangSmith tracing.
    def traceable(*args, **kwargs):  # type: ignore[misc]
        def decorator(fn):
            return fn
        return decorator

from orchestrator.cache.response_cache import ResponseCache
from orchestrator.cache.tool_cache import ToolCache
from orchestrator.core.stage import StageResult, StageStatus
from orchestrator.core.state import OrchestratorState
from orchestrator.gateway.models import GatewayRequest, GatewayResponse
from orchestrator.memory.store import MemoryStore
from orchestrator.prompt_builder.builder import PromptBuilder
from orchestrator.tools.registry import ToolRegistry


class _GatewayCallable(Protocol):
    """Minimal interface StageAgent needs from AIGateway.

    Using a Protocol rather than importing AIGateway directly avoids pulling
    in the openai dependency at import time — tests pass MagicMock without
    openai installed.
    """
    def call(self, request: GatewayRequest) -> GatewayResponse: ...

MAX_TOOL_ITERATIONS = 10


class StageAgent:
    """Runs one pipeline stage: build prompt → LLM loop → parse artifact → StageResult."""

    def __init__(
        self,
        gateway: _GatewayCallable,
        tool_registry: ToolRegistry,
        response_cache: ResponseCache,
        prompt_builder: PromptBuilder | None = None,
        model: str = "gpt-4o",
        max_tool_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> None:
        self._gateway = gateway
        self._registry = tool_registry
        self._cache = response_cache
        self._builder = prompt_builder or PromptBuilder()
        self._model = model
        self._max_iterations = max_tool_iterations

    @traceable(name="stage_agent.run")
    def run(
        self,
        stage_name: str,
        state: OrchestratorState,
        memory_store: MemoryStore,
        attempt_number: int = 1,
    ) -> StageResult:
        """Execute a stage and return its result.

        Args:
            stage_name:     e.g. "architecture_design"
            state:          Current OrchestratorState (requirement, artifacts, etc.)
            memory_store:   MemoryStore for Layer 3 prompt injection.
            attempt_number: 1-based retry counter (recorded in StageResult).

        Returns:
            StageResult with status COMPLETED (success) or FAILED (max retries hit
            or unrecoverable error).
        """
        started_at = datetime.now(timezone.utc)

        # ── Step 1: Build prompt ───────────────────────────────────────────────
        messages, prompt_version = self._builder.build(
            stage_name, state, memory_store
        )

        # ── Step 2: Check response cache ──────────────────────────────────────
        cache_key = _prompt_cache_key(messages)
        cached = self._cache.get(cache_key, self._model)
        if cached is not None:
            return StageResult(
                stage_name=stage_name,
                status=StageStatus.COMPLETED,
                attempt_number=attempt_number,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                output_artifact=cached,
                prompt_version=prompt_version,
                model_used=self._model,
            )

        # ── Steps 3–5: Multi-turn tool-calling loop ───────────────────────────
        tool_cache = ToolCache(state.get("tool_cache", {}))
        conversation_history: list[dict] = []
        artifact: dict | None = None
        error_message: str | None = None

        try:
            for iteration in range(self._max_iterations):
                current_messages, _ = self._builder.build(
                    stage_name, state, memory_store,
                    conversation_history=conversation_history,
                )

                response = self._gateway.call(
                    GatewayRequest(
                        token=state.get("triggered_by", ""),
                        run_id=state["run_id"],
                        stage_name=stage_name,
                        messages=current_messages,
                        model=self._model,
                        prompt_version=prompt_version,
                        tools=self._registry.get_schemas(),
                    )
                )

                # No tool calls → final response
                if not response.tool_calls:
                    artifact = _parse_artifact(stage_name, response.content or "")
                    break

                # Execute tool calls and append results to history
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                }
                conversation_history.append(assistant_msg)

                for tc in response.tool_calls:
                    tool_name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}

                    tool_result = self._registry.execute(tool_name, args, tool_cache)
                    result_content = (
                        str(tool_result.error)
                        if tool_result.error
                        else json.dumps(tool_result.result)
                    )
                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_content,
                    })

            else:
                raise RuntimeError(
                    f"StageAgent: stage '{stage_name}' hit the {self._max_iterations}-iteration "
                    f"tool-call limit without producing a final response."
                )

        except Exception as exc:
            return StageResult(
                stage_name=stage_name,
                status=StageStatus.FAILED,
                attempt_number=attempt_number,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                error_message=str(exc),
                prompt_version=prompt_version,
                model_used=self._model,
            )

        # ── Step 6: Cache the response ────────────────────────────────────────
        if artifact is not None:
            self._cache.set(cache_key, self._model, artifact)

        # Persist updated tool_cache back into state
        state["tool_cache"] = tool_cache.to_dict()

        return StageResult(
            stage_name=stage_name,
            status=StageStatus.COMPLETED,
            attempt_number=attempt_number,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            output_artifact=artifact,
            prompt_version=prompt_version,
            model_used=self._model,
        )


# ── module-level helpers ──────────────────────────────────────────────────────


def _prompt_cache_key(messages: list[dict]) -> str:
    """Stable string key for the full messages list — used as ResponseCache prompt_text."""
    return json.dumps(messages, sort_keys=True, ensure_ascii=False)


def _parse_artifact(stage_name: str, raw: str) -> dict:
    """Strip markdown fences and parse JSON artifact from LLM response.

    Raises:
        ValueError: if the response is not valid JSON after fence stripping.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"StageAgent: malformed JSON artifact from '{stage_name}': {exc}\n"
            f"--- raw response ---\n{raw}"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError(
            f"StageAgent: artifact for '{stage_name}' must be a JSON object, "
            f"got {type(result).__name__}"
        )
    return result

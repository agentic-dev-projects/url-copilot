"""
orchestrator.agents — Thin per-stage LLM caller.

Files
-----
stage_agent.py  StageAgent — the only class in this package.

StageAgent does exactly one thing: given a stage name and RunContext, it
builds the prompt (via PromptBuilder), calls the AI Gateway, handles the
multi-turn tool-call loop, and returns a typed StageResult.

What it deliberately does NOT do:
  - Auth, rate limiting, guardrails  →  handled by AIGateway
  - Retries                          →  handled by OrchestrationEngine
  - Persistence                      →  handled by RunStateStore
  - Human approval                   →  handled by RBACCheckpoint / HybridGate

This narrow scope means StageAgent is easy to test in isolation: mock the
gateway response and the tool registry, assert the StageResult is correct.

Multi-turn tool-call loop
--------------------------
OpenAI may respond with tool_calls instead of a final answer.  StageAgent
loops until the response contains no tool calls:

    while response has tool_calls:
        execute each tool via ToolRegistry
        append results to conversation history
        call gateway again with updated messages
    parse final text response → StageResult.output_artifact

Implemented in Phase 12.
"""

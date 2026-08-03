"""
orchestrator — AI SDLC Orchestration Layer for url-copilot.

This package accepts a natural-language engineering requirement, classifies it,
decomposes it into a DAG of SDLC stages, executes each stage using OpenAI as the
agent, enforces human approval gates with RBAC, writes code changes to service/,
creates a GitHub PR, and produces a full audit trail.

Sub-packages
------------
gateway         Central AI Gateway: auth, RBAC, rate limiting, guardrails, tracing
planner         Requirement classifier and DAG selector
core            DAG engine, stage models, RunContext shared state
agents          Thin per-stage LLM caller
evaluator       Hybrid LLM-as-Judge: ValidatorAgent (o1-mini) + HybridGate
governance      RBAC checkpoints and append-only SDLC audit logger
state           PostgreSQL persistence for all orch_ tables
memory          Cross-run memory store (facts, preferences, decisions)
cache           Response cache (PostgreSQL) and tool result cache (in-memory)
tools           Tool registry: read_file, write_file, run_tests, GitHub ops
prompt_builder  7-layer prompt assembly with static prefix caching
scenarios       Greenfield, Brownfield, Ambiguous DAG definitions
metrics         Run-level observability aggregation

Entry point
-----------
CLI:  python -m orchestrator.run "<requirement>" --token <user_token>
"""

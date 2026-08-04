"""
orchestrator.core — DAG engine, stage models, and shared run state.

Files
-----
stage.py    StageStatus enum, StageNode dataclass, StageResult dataclass.
            StageNode describes a stage before execution (name, deps, gate,
            retries).  StageResult is the execution record persisted to
            orch_stage_results after each attempt.

state.py    OrchestratorState TypedDict — the LangGraph shared state passed
            between every pipeline node.  Replaces the RunContext dataclass.
            LangGraph checkpoints this to PostgreSQL via PostgresSaver after
            every node execution, enabling run resumption after crashes or
            human gate pauses.

engine.py   OrchestrationEngine — builds the LangGraph StateGraph, registers
            all nodes and edges, compiles the graph with PostgresSaver, and
            invokes it.  Replaces the custom DAG execution loop.
            Implemented in Phase 13.

Why LangGraph instead of a custom DAG?
----------------------------------------
LangGraph provides native parallel fan-out (multiple nodes run concurrently
when their shared upstream dependency completes), built-in interrupt() for
human-in-the-loop gates, automatic state checkpointing to PostgreSQL, and
run resumption from any checkpoint.  Building these from scratch in Phase 13
would have been ~400 lines.  With LangGraph it is a declarative graph
definition of ~80 lines.

LangSmith integration (automatic when env vars are set)
--------------------------------------------------------
Set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY in .env.
Every LLM call made through any node is automatically traced — inputs,
outputs, token counts, cost, and latency visible in the LangSmith dashboard.
No code instrumentation required.

Implemented in Phase 3 (stage, state) and Phase 13 (engine).
"""

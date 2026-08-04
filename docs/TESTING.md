# Testing Guide

This project has two tiers of tests:

| Tier | Infrastructure needed | Cost | Run time |
|---|---|---|---|
| **Unit / integration** | Python only (SQLite in-memory) | Free | < 30 seconds |
| **Live E2E** | PostgreSQL + OpenAI API key | ~$0.01–$0.40 per run | 3–8 minutes |

Everything in tier 1 runs without Docker, without a database, and without any API keys.
Tier 2 is guarded behind `RUN_E2E=1` so it never executes accidentally in CI.

---

## Prerequisites

### For all tests (tier 1)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Additional setup for live E2E tests (tier 2)

1. **Start PostgreSQL**

   ```bash
   export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
   docker compose up db -d
   ```

2. **Run migrations** (creates all `orch_*` tables)

   ```bash
   alembic upgrade head
   ```

3. **Install langgraph and openai**

   ```bash
   pip install langgraph openai
   ```

4. **Set your OpenAI API key in `.env`**

   ```
   OPENAI_API_KEY=sk-...
   ```

---

## Tier 1 — Unit and Integration Tests

All tests in this tier use SQLite in-memory.  No Docker, no API keys.

### Service layer (45 tests)

The URL shortener business logic, API routes, and authentication.

```bash
# Run the full service suite
.venv/bin/python -m pytest service/tests/ -v
```

Run individual modules:

```bash
# Unit: security helpers (hashing, token generation)
.venv/bin/python -m pytest service/tests/unit/test_security.py -v

# Unit: short-code generation and collision logic
.venv/bin/python -m pytest service/tests/unit/test_url_generator.py -v

# Unit: URL service business logic (create, redirect, analytics)
.venv/bin/python -m pytest service/tests/unit/test_url_service.py -v

# Integration: auth endpoints (register, API key validation)
.venv/bin/python -m pytest service/tests/integration/test_auth.py -v

# Integration: URL endpoints (shorten, redirect, analytics)
.venv/bin/python -m pytest service/tests/integration/test_urls.py -v
```

### Orchestrator unit tests (276 tests across 12 modules)

The AI orchestration system — gateway, planner, memory, agents, tools, metrics.

```bash
# Run the entire orchestrator unit suite (excludes E2E)
.venv/bin/python -m pytest orchestrator/tests/ --ignore=orchestrator/tests/test_e2e_live.py -v
```

Run individual modules (useful when iterating on a specific component):

```bash
# Gateway pipeline components (rate limiter, guardrails, cost tracker, retry)
.venv/bin/python -m pytest orchestrator/tests/test_gateway_components.py -v

# Token auth + RBAC (four-eyes constraint, permission checks)
.venv/bin/python -m pytest orchestrator/tests/test_auth_rbac.py -v

# Requirement classifier + clarification loop + planner
.venv/bin/python -m pytest orchestrator/tests/test_planner.py -v

# Stage agent (LLM call → tool dispatch → artifact writing)
.venv/bin/python -m pytest orchestrator/tests/test_stage_agent.py -v

# Tool registry (read_file, write_file, run_tests, run_linter, etc.)
.venv/bin/python -m pytest orchestrator/tests/test_tools.py -v

# Prompt builder (codebase context injection, memory injection)
.venv/bin/python -m pytest orchestrator/tests/test_prompt_builder.py -v

# LangGraph orchestration engine (run, resume, get_state)
.venv/bin/python -m pytest orchestrator/tests/test_engine.py -v

# Response cache (hit/miss/invalidation)
.venv/bin/python -m pytest orchestrator/tests/test_cache.py -v

# Memory store (seed, read, write, prune)
.venv/bin/python -m pytest orchestrator/tests/test_memory_store.py -v

# Run state store (orch_runs + orch_stage_results CRUD)
.venv/bin/python -m pytest orchestrator/tests/test_state_store.py -v

# Metrics tracker (summarize, MTTR, success rate)
.venv/bin/python -m pytest orchestrator/tests/test_metrics.py -v

# Audit logger (event writes + query)
.venv/bin/python -m pytest orchestrator/tests/test_audit.py -v
```

### Run the full tier 1 suite in one command

```bash
.venv/bin/python -m pytest service/tests/ orchestrator/tests/ \
  --ignore=orchestrator/tests/test_e2e_live.py -v
```

---

## Tier 2 — Live E2E Tests (7 tests)

These tests make real OpenAI API calls and write to PostgreSQL.
They are organized into five test classes ordered by cost, cheapest first.

> **The `RUN_E2E=1` guard**
> Without the prefix, pytest collects all 7 tests and immediately skips them
> (`7 skipped in 0.02s`) — safe to run any time.
> With `RUN_E2E=1`, the guard lifts and all 7 run against live infrastructure.

### A — Classifier smoke tests (cheap, ~3 API calls, gpt-4o-mini)

Verifies that `RequirementClassifier` correctly buckets all three scenario types.

```bash
RUN_E2E=1 .venv/bin/python -m pytest \
  orchestrator/tests/test_e2e_live.py::TestClassifierScenarios -v -s
```

Expected output:

```
[greenfield] scenario=greenfield confidence=0.95
[brownfield] scenario=brownfield confidence=0.88
[ambiguous]  scenario=ambiguous  confidence=0.52  clarification: Which area...
3 passed in ~6s
```

### B — Clarification loop (cheap, ~2 API calls, gpt-4o-mini)

Verifies the two-call loop: generate questions → inject fixed answers → resolve to a scoped requirement.

```bash
RUN_E2E=1 .venv/bin/python -m pytest \
  orchestrator/tests/test_e2e_live.py::TestClarificationLoop -v -s
```

### C — Greenfield full pipeline (expensive, ~14 API calls, gpt-4o)

Runs the complete 14-node LangGraph pipeline for a new-feature requirement.
Verifies fan-out/fan-in routing, all stage artifacts, PostgreSQL writes, and MetricsTracker.

```bash
RUN_E2E=1 .venv/bin/python -m pytest \
  orchestrator/tests/test_e2e_live.py::TestGreenfieldFullPipeline -v -s
```

Estimated time: 3–8 minutes. Estimated cost: $0.10–$0.40.

### D — Brownfield full pipeline (expensive, ~14 API calls, gpt-4o)

Same 14-node topology as greenfield. Verifies the classifier routes to `BrownfieldScenario`.

```bash
RUN_E2E=1 .venv/bin/python -m pytest \
  orchestrator/tests/test_e2e_live.py::TestBrownfieldFullPipeline -v -s
```

Estimated time: 3–8 minutes. Estimated cost: $0.10–$0.40.

### E — Ambiguous full pipeline (expensive, ~16 API calls, gpt-4o)

Runs clarification first (2 extra calls), then the full pipeline on the resolved requirement.

```bash
RUN_E2E=1 .venv/bin/python -m pytest \
  orchestrator/tests/test_e2e_live.py::TestAmbiguousFullPipeline -v -s
```

Estimated time: 3–8 minutes. Estimated cost: $0.10–$0.40.

### Run all 7 E2E tests

```bash
RUN_E2E=1 .venv/bin/python -m pytest \
  orchestrator/tests/test_e2e_live.py -v -s
```

---

## What is mocked in E2E tests

The E2E tests mock only write-side-effect tools so they never mutate the codebase
or call GitHub.  All AI calls, DB reads/writes, and codebase file reads are real.

| Component | Real or Mocked | Why |
|---|---|---|
| OpenAI API calls | **Real** | Core thing being tested |
| PostgreSQL reads/writes | **Real** | Verifies audit trail, metrics, state |
| Codebase file reads | **Real** | LLM receives genuine context |
| `interrupt()` / human gates | Mocked (auto-approve) | No CLI interaction in tests |
| `write_file` | Mocked (no-op) | Prevents mutating `service/` source |
| `run_tests` | Mocked (returns 45 passed) | Avoids recursive pytest call |
| `run_linter` | Mocked (no violations) | Avoids subprocess overhead |
| `create_branch` | Mocked (201) | No GitHub token needed |
| `create_pr` | Mocked (201 + fake URL) | No GitHub token needed |

---

## Why this two-tier structure is correct

**Unit tests should be free to run.**
A test suite that requires API keys, running containers, or network access will get
skipped by developers iterating locally.  All 321 unit/integration tests in tier 1
run in under 30 seconds with nothing but `pip install` — no excuses to skip them.

**E2E tests should test the real thing.**
Mocking the LLM in E2E tests defeats the purpose — it would only verify that the
mock returns the expected value.  The classifier, clarification loop, and stage agent
need to run against real OpenAI to verify that prompts produce the right structure,
that costs are tracked correctly, and that the full pipeline completes without errors.

**`RUN_E2E=1` is explicit opt-in.**
The env var makes the cost and intent visible.  CI (GitHub Actions, etc.) never sets
`RUN_E2E=1`, so these tests are always skipped in automated pipelines unless you
add a dedicated workflow step for it.  Local developers run them when they change
the gateway, planner, or pipeline topology — not on every commit.

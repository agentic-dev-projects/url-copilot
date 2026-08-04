# Implementation Plan — url-copilot Orchestrator

> **Purpose**: This document is the continuation guide for implementing the orchestrator.
> Read `docs/orchestrator-architecture.md` for the full design.
> This file tracks what is done, what is next, and the exact build order.

---

## Current State

| Component | Status | Notes |
|---|---|---|
| `service/` — URL shortener | ✅ COMPLETE | 45 tests passing, alembic migration ready |
| `orchestrator/` — AI orchestrator | ❌ NOT STARTED | All phases below pending |
| `docs/orchestrator-architecture.md` | ✅ COMPLETE | Full design reference |
| `docs/scenarios/greenfield.md` | ✅ COMPLETE | Full scenario walkthrough |
| `docs/scenarios/brownfield.md` | ✅ COMPLETE | Full scenario walkthrough |
| `docs/scenarios/ambiguous.md` | ✅ COMPLETE | Full scenario walkthrough |
| `docs/IMPLEMENTATION_PLAN.md` | ✅ COMPLETE | This file |

---

## Dependencies to Add to `requirements.txt`

Before starting implementation, add these to `requirements.txt`:

```
openai>=1.40.0
PyYAML>=6.0
PyGithub>=2.3.0
prometheus_fastapi_instrumentator>=7.0.0
pytest-asyncio>=0.23.0
```

---

## Build Phases

Phases must be executed in order. Each phase is independently testable.

---

### Phase 1 — Config Files and Directory Skeleton
**Status**: ❌ TODO
**No code logic — just YAML files and `__init__.py` stubs.**

Files to create:
```
orchestrator/__init__.py
orchestrator/config/__init__.py
orchestrator/config/users.yaml          ← see Section 17 of architecture doc
orchestrator/config/rbac.yaml           ← see Section 17 of architecture doc
orchestrator/config/models.yaml         ← see Section 17 of architecture doc
orchestrator/config/evaluator.yaml      ← see Section 17 of architecture doc
orchestrator/memory/__init__.py
orchestrator/memory/seeds.yaml          ← see Section 17 of architecture doc
orchestrator/gateway/__init__.py
orchestrator/planner/__init__.py
orchestrator/core/__init__.py
orchestrator/agents/__init__.py
orchestrator/governance/__init__.py
orchestrator/state/__init__.py
orchestrator/metrics/__init__.py
orchestrator/cache/__init__.py
orchestrator/tools/__init__.py
orchestrator/prompt_builder/__init__.py
orchestrator/evaluator/__init__.py
orchestrator/scenarios/__init__.py
orchestrator/prompts/                   ← empty directory, files added in Phase 9
orchestrator/prompts/evaluator/         ← empty directory, files added in Phase 3.5
```

**Verify**: `python -c "import orchestrator"` should not error.

---

### Phase 2 — Database Migration for Orchestrator Tables
**Status**: ❌ TODO
**Creates the 6 `orch_` PostgreSQL tables.**

1. Create new Alembic migration:
   ```bash
   alembic revision --autogenerate -m "orch_tables"
   ```
   **Important**: The autogenerate will NOT work because the orch_ models don't exist yet.
   Instead, manually create the migration file using the exact DDL from
   Section 6 of `docs/orchestrator-architecture.md`.

2. Create file: `service/db/migrations/versions/{date}_orch_tables.py`

   The `upgrade()` function must create these tables in dependency order:
   ```
   orch_runs       (standalone)
   orch_stage_results  (FK → orch_runs)
   orch_audit_events   (FK → orch_runs)
   orch_metrics        (FK → orch_runs)
   orch_memory         (FK → orch_runs, nullable)
   orch_cache      (standalone)
   ```

   Note: no `orch_users` table. User identity is resolved from `config/users.yaml`
   by `TokenAuthenticator` at auth time and passed as a `CurrentUser` dataclass.
   Keeping users only in YAML avoids a dual source of truth problem.

   The `downgrade()` function drops in reverse order.

3. Apply migration:
   ```bash
   alembic upgrade head
   ```

**Verify**: `alembic current` shows two migrations applied. All 6 `orch_` tables exist in PostgreSQL.

---

### Phase 3 — Core Data Models
**Status**: ❌ TODO
**Pure Python dataclasses. No external dependencies. No DB. Testable in isolation.**

Files to create:

**`orchestrator/core/stage.py`**:
```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

class StageStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"

@dataclass
class StageNode:
    name: str
    depends_on: list[str] = field(default_factory=list)
    status: StageStatus = StageStatus.PENDING
    requires_gate: str | None = None        # permission name if gate required
    max_attempts: int = 3

@dataclass
class StageResult:
    stage_name: str
    status: StageStatus
    attempt_number: int
    started_at: datetime
    completed_at: datetime | None = None
    output_artifact: dict[str, Any] | None = None
    error_message: str | None = None
    prompt_version: str | None = None
    model_used: str | None = None
```

**`orchestrator/core/state.py`** _(replaces context.py — LangGraph requires TypedDict)_:
```python
from typing import Any
from typing_extensions import TypedDict

class OrchestratorState(TypedDict, total=False):
    run_id:                 str
    requirement:            str
    resolved_requirement:   str
    scenario_type:          str
    triggered_by:           str
    stage_artifacts:        dict[str, Any]
    stage_evaluations:      dict[str, Any]
    feature_branch:         str | None
    pr_url:                 str | None
    pr_number:              int | None
    schema_change_detected: bool
    assumptions:            list[str]
    tool_cache:             dict[str, Any]  # in-memory only, not checkpointed by LangGraph
```

`total=False` means every key is optional — each pipeline node returns only the keys it changed and LangGraph merges the partial update automatically.

> **Note**: `context.py` (RunContext dataclass) and `dag.py` (DAGGraph) were planned here but deleted when we switched to LangGraph. `state.py` replaces both: LangGraph manages the graph topology (replacing DAGGraph) and checkpoints OrchestratorState to PostgreSQL after every node (replacing manual RunContext passing).

**Verify**:
```python
from orchestrator.core.state import OrchestratorState
state: OrchestratorState = {"run_id": "test-123", "requirement": "Add QR endpoint"}
assert state["run_id"] == "test-123"
assert state.get("schema_change_detected") is None  # total=False — key absent until set
```

---

### Phase 3.5 — Evaluator Component (Hybrid LLM-as-Judge)
**Status**: ❌ TODO
**Pure Python dataclasses + one LLM call. No DB reads/writes (audit events handled by existing AuditLogger from Phase 5). Build after Phase 3 (data models exist) and before Phase 4 (used by engine in Phase 13).**

This phase implements the three-file hybrid evaluation component described in Section 5.12 of `docs/orchestrator-architecture.md`.

Files to create:

**`orchestrator/evaluator/__init__.py`** — empty

**`orchestrator/evaluator/evaluation_report.py`**:
```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class EvaluationReport:
    stage_name: str
    overall_score: int                  # 1–5
    strengths: list[str]
    concerns: list[str]
    blocking_issues: list[str]
    suggestions: list[str]
    recommendation: Literal["APPROVE", "APPROVE_WITH_NOTES", "REJECT"]

@dataclass
class HybridFeedback:
    stage_name: str
    ai_evaluation: EvaluationReport
    human_comment: str
    approved_by: str
    override: bool = False
```

**`orchestrator/evaluator/validator_agent.py`** — `ValidatorAgent`:
```python
class ValidatorAgent:
    def __init__(self, gateway: AIGateway, config: dict):
        self.gateway = gateway
        self.validator_model = config["validator_model"]   # "o1-mini"
        self.prompt_map = config["prompts"]

    def evaluate(
        self,
        stage_name: str,
        stage_artifact: dict,
        context: RunContext,
        audit: AuditLogger,
    ) -> EvaluationReport:
        # 1. Load critic prompt from orchestrator/prompts/evaluator/{prompt_file}
        # 2. Build messages: system=critic_prompt, user=json.dumps(stage_artifact)
        # 3. audit.log(EVALUATOR_STARTED, stage_name)
        # 4. gateway.call(model=self.validator_model, messages=messages)
        # 5. Parse JSON response → EvaluationReport dataclass
        # 6. audit.log(EVALUATOR_COMPLETED, stage_name, details={"score": report.overall_score})
        # 7. Return report
```

The critic prompts instruct `o1-mini` to return a JSON object with keys:
`overall_score`, `strengths`, `concerns`, `blocking_issues`, `suggestions`, `recommendation`.

Create these prompt files:
```
orchestrator/prompts/evaluator/eval_architecture_v1.txt
orchestrator/prompts/evaluator/eval_implementation_v1.txt
orchestrator/prompts/evaluator/eval_tests_v1.txt
orchestrator/prompts/evaluator/eval_release_v1.txt
```

Each prompt starts with: `You are a senior software engineer performing a critical review. You must be thorough but fair. Return ONLY valid JSON matching the schema provided.` Then includes stage-specific instructions.

**`orchestrator/evaluator/hybrid_gate.py`** — `HybridGate`:
```python
class HybridGate:
    def __init__(self, validator: ValidatorAgent, checkpoint: RBACCheckpoint, audit: AuditLogger, memory: MemoryStore):
        ...

    def run(
        self,
        stage_name: str,
        stage_artifact: dict,
        context: RunContext,
        required_permission: str,
        approver_token: str,
    ) -> HybridFeedback:
        # 1. validator.evaluate(stage_name, stage_artifact, context, audit)
        # 2. Print AI evaluation to CLI (score, strengths, concerns, blocking_issues)
        # 3. checkpoint: verify approver role + four-eyes
        # 4. Prompt human: comment + [y/n]
        # 5. If approved AND blocking_issues present → override=True
        #    → audit.log(CHECKPOINT_APPROVED_OVERRIDE, details={...})
        # 6. If approved, no blocking → audit.log(CHECKPOINT_APPROVED)
        # 7. If rejected → audit.log(CHECKPOINT_REJECTED) → raise ApprovalRejectedError
        # 8. If human_comment → memory.save(type="preference", actor=approver, content=comment)
        # 9. feedback = HybridFeedback(stage_name, report, comment, approver, override)
        # 10. context.stage_evaluations[stage_name] = feedback
        # 11. return feedback
```

Add `orchestrator/config/evaluator.yaml` (content in Section 17 of architecture doc).

**Verify**:
```python
# Mock the gateway call to return a sample EvaluationReport JSON.
# Confirm ValidatorAgent parses it correctly into EvaluationReport.
# Confirm HybridFeedback is built and saved to context.stage_evaluations.
# Confirm EVALUATOR_STARTED and EVALUATOR_COMPLETED audit events are logged.
```

---

### Phase 4 — State Store
**Status**: ✅ Done
**PostgreSQL reads and writes for orch_runs and orch_stage_results. Uses existing `service.db.session.SessionLocal`.**

File: **`orchestrator/state/store.py`**

`RunStateStore` with these methods:
```python
def create_run(run_id, requirement, scenario_type, triggered_by) -> None
def update_run_status(run_id, status) -> None
def update_run_completed(run_id, feedback_score, feedback_comment) -> None
def update_run_pr(run_id, pr_url, feature_branch) -> None
def save_stage_result(result: StageResult, run_id: str) -> None
def get_run(run_id: str) -> dict
def get_stage_result(run_id: str, stage_name: str) -> dict | None
def get_all_stage_results(run_id: str) -> list[dict]
def load_run_state(run_id: str) -> OrchestratorState   # replaces load_run_context (RunContext deleted)
```

Module-level `make_store()` context manager — commit on success, rollback on exception.

Uses raw SQL via `sqlalchemy.text()`. No new ORM models — tables created via raw DDL in Phase 2.

`load_run_state()` returns `OrchestratorState` TypedDict (business view of run state for CLI
`approve` command).  LangGraph `PostgresSaver` handles the technical graph resume checkpoint separately.

**Verify** (`orchestrator/tests/test_state_store.py` — 13 integration tests, requires PostgreSQL):
```bash
docker-compose up -d db
.venv/bin/python -m pytest orchestrator/tests/test_state_store.py -v
```

---

### Phase 5 — Governance: Audit Logger
**Status**: ✅ Done

File: **`orchestrator/governance/audit.py`**

`AuditLogger` with:
```python
def log(run_id, event_type, actor="system", stage_name=None, actor_role=None, details=None) -> None
def get_events(run_id) -> list[dict]   # read-only; used in tests and CLI summary
```

`EventType(str, Enum)` — 18 constants. Inherits `str` so `EventType.STAGE_STARTED == "STAGE_STARTED"`
(no `.value` needed in SQL or print statements).

Writes to `orch_audit_events` table. **Constraint**: `log()` only ever INSERTs — never UPDATEs or
DELETEs. No `update()` or `delete()` method exists on the class. This structural guarantee
satisfies SOC2 CC7.2.

`make_audit_logger()` context manager follows the same pattern as `make_store()`.

**Verify** (`orchestrator/tests/test_audit.py` — 11 integration tests, requires PostgreSQL):
```bash
.venv/bin/python -m pytest orchestrator/tests/test_audit.py -v
```

---

### Phase 6 — Gateway: Auth and RBAC
**Status**: ✅ Done

**`orchestrator/gateway/auth.py`** — `TokenAuthenticator` + `CurrentUser` + `AuthenticationError`:
- Loads `users.yaml` + `rbac.yaml` once at construction
- `resolve(token) -> CurrentUser` — raises `AuthenticationError` if not found
- `_expand_permissions(role)` — recursively walks `inherits` chain, unions all permission sets
- `CurrentUser`: `github_login`, `email`, `role`, `permissions: list[str]`, `daily_token_budget`

**`orchestrator/governance/checkpoint.py`** — `RBACCheckpoint` + `AuthorizationError` + `FourEyesViolationError`:
- `check_permission(user, permission)` — raises `AuthorizationError` if not in user.permissions
- `verify_four_eyes(approver_login, triggered_by)` — raises `FourEyesViolationError` if same
- `request_approval(*, run_id, required_permission, trigger_user, approver_token) -> str` — resolves token, checks permission, four-eyes, returns approver github_login. CLI prompting and audit logging are `HybridGate`'s responsibility.

**Verify** (`orchestrator/tests/test_auth_rbac.py` — 22 unit tests, no DB needed):
```bash
.venv/bin/python -m pytest orchestrator/tests/test_auth_rbac.py -v
# 22 passed in 0.06s
```

---

### Phase 7 — Gateway: Full Pipeline
**Status**: ✅ COMPLETE
**Builds the complete 11-layer pre/post-call pipeline.**

Files to create (implement in this order):

1. **`orchestrator/gateway/tracer.py`** — `RequestTracer`
   - `start(request) -> str` returns trace_id (UUID4)
   - `end(trace_id) -> float` returns duration_ms

2. **`orchestrator/gateway/logger.py`** — `StructuredLogger`
   - `log_request(trace_id, user, request) -> None` — JSON to stdout
   - `log_response(trace_id, response) -> None` — JSON to stdout

3. **`orchestrator/gateway/rate_limiter.py`** — `RateLimiter`
   - In-memory `dict[str, list[float]]` — timestamps of recent calls per user
   - `check(user: CurrentUser) -> None` — raises `RateLimitError` if > 20 calls/minute

4. **`orchestrator/gateway/token_budget.py`** — `TokenBudgetManager`
   - `check(user, estimated_tokens) -> None` — reads today's sum from `orch_metrics`
   - `record(user, tokens_used) -> None` — updates `orch_metrics`
   - Raises `TokenBudgetExceededError` if over daily limit for role

5. **`orchestrator/gateway/input_validator.py`** — `InputValidator`
   - Schema: requirement must be 10–2000 chars, no null bytes
   - Injection patterns: see Section 9 of architecture doc
   - Raises `PromptInjectionError` if detected

6. **`orchestrator/gateway/guardrails.py`** — `GuardrailChecker`
   - `check_input(prompt) -> None` — PII patterns: SSN, credit card, email in prompt
   - `check_output(content) -> None` — dangerous code patterns, PII in response
   - Patterns: `os.system(`, `subprocess.`, `eval(`, `rm -rf`, `DROP TABLE`, hardcoded secrets
   - Raises `GuardrailViolationError` if any pattern found

7. **`orchestrator/gateway/cost_tracker.py`** — `CostTracker`
   - Price table: `{"gpt-4o": {"input": 0.0025, "output": 0.010}}` per 1K tokens
   - `record(trace_id, user, run_id, stage_name, usage, model) -> None`
   - Writes row to `orch_metrics`

8. **`orchestrator/gateway/gateway.py`** — `AIGateway`
   - Assembles all components above
   - At startup: wrap the OpenAI client with `langsmith.wrappers.wrap_openai(openai.OpenAI())` — this single call enables automatic LangSmith tracing for every subsequent OpenAI API call (inputs, outputs, token counts, cost, latency) with zero additional instrumentation
   - `call(request: GatewayRequest) -> GatewayResponse`
   - Full pipeline as described in Section 5.1 of architecture doc

**GatewayRequest dataclass**:
```python
@dataclass
class GatewayRequest:
    token: str
    run_id: str
    stage_name: str
    messages: list[dict]        # OpenAI messages format
    tools: list[dict] | None    # OpenAI function definitions
    model: str                  # from models.yaml
    prompt_version: str
```

**GatewayResponse dataclass**:
```python
@dataclass
class GatewayResponse:
    content: str | None
    tool_calls: list[dict] | None
    usage: dict                 # {"input_tokens": int, "output_tokens": int}
    trace_id: str
    cache_hit: bool
```

**Verify**: Call `gateway.call()` with a mock OpenAI response. Confirm all 11 pipeline steps execute and write to DB.

---

### Phase 8 — Memory System
**Status**: ✅ COMPLETE

File: **`orchestrator/memory/store.py`** — `MemoryStore`:
```python
def seed_if_empty() -> None:
    # Read seeds.yaml, check if orch_memory is empty, INSERT seeds if so

def save(memory_type, actor, content, source_run_id=None) -> None:
    # INSERT into orch_memory

def load_all_active() -> list[dict]:
    # SELECT * FROM orch_memory WHERE is_active = TRUE ORDER BY created_at

def format_for_prompt() -> str:
    # Returns formatted string block for Layer 3 of Prompt Builder
    # Format: "=== Team Conventions and Preferences ===\n[type] content (actor)"
```

Call `seed_if_empty()` once at orchestrator startup (in `run.py`).

**Verify**: Seed the table. Call `format_for_prompt()`. Confirm all 5 seed facts appear.

---

### Phase 9 — Cache
**Status**: ✅ COMPLETE

**`orchestrator/cache/response_cache.py`** — `ResponseCache`:
```python
def get(prompt_text: str, model: str) -> dict | None:
    # Hash SHA-256(prompt_text + model)
    # SELECT from orch_cache WHERE prompt_hash = ? AND expires_at > now()
    # If found: UPDATE hit_count += 1, return response JSONB

def set(prompt_text: str, model: str, response: dict) -> None:
    # INSERT into orch_cache with expires_at = now() + 24h
```

**`orchestrator/cache/tool_cache.py`** — `ToolCache`:
```python
# In-memory dict, scoped to RunContext.tool_cache
def get(tool_name: str, args: dict) -> Any | None:
    key = sha256(tool_name + json.dumps(args, sort_keys=True))
    return context.tool_cache.get(key)

def set(tool_name: str, args: dict, result: Any) -> None:
    key = sha256(tool_name + json.dumps(args, sort_keys=True))
    context.tool_cache[key] = result
```

**Verify**: Set a response cache entry, retrieve it, confirm `cache_hit=True` returned. Confirm expired entries are not returned.

---

### Phase 10 — Tool Registry
**Status**: ✅ COMPLETE

**`orchestrator/tools/filesystem.py`**:
```python
PROJECT_ROOT = Path(__file__).parent.parent.parent  # url-copilot root

def read_file(path: str) -> str:
    # Resolve path relative to PROJECT_ROOT
    # Return file contents as string

def write_file(path: str, content: str) -> bool:
    # Resolve path — MUST be under service/ directory
    # If outside service/: raise GuardrailError("write outside service/ not permitted")
    # Write content, return True

def search_codebase(query: str) -> list[dict]:
    # grep -rn query service/ --include="*.py"
    # Return list of {"file": str, "line": int, "content": str}
```

**`orchestrator/tools/test_runner.py`**:
```python
def run_tests(path: str = "service/tests/") -> dict:
    # subprocess.run(["python", "-m", "pytest", path, "-v", "--tb=short"])
    # Parse output: return {"passed": int, "failed": int, "output": str}

def run_linter() -> dict:
    # subprocess.run(["python", "-m", "flake8", "service/", "--max-line-length=120"])
    # Return {"passed": bool, "violations": list[str]}
```

**`orchestrator/tools/github_client.py`**:
```python
# Uses GITHUB_TOKEN from .env and GITHUB_REPO from .env
# Uses PyGithub library

def create_branch(branch_name: str) -> str:
    # Create branch from main, return branch URL

def create_pr(title: str, body: str, branch: str) -> tuple[int, str]:
    # Create PR, return (pr_number, pr_url)

def poll_pr_status(pr_number: int) -> dict:
    # Return {"merged": bool, "merged_by": str | None, "closed": bool}
```

**`orchestrator/tools/registry.py`** — `ToolRegistry`:
```python
# Maps tool name → function
# Wraps all calls with tool latency tracking
TOOLS = {
    "read_file": filesystem.read_file,
    "write_file": filesystem.write_file,
    "search_codebase": filesystem.search_codebase,
    "run_tests": test_runner.run_tests,
    "run_linter": test_runner.run_linter,
    "create_branch": github_client.create_branch,
    "create_pr": github_client.create_pr,
    "poll_pr_status": github_client.poll_pr_status,
}

def call(tool_name: str, args: dict, context: RunContext, metrics_writer) -> Any:
    # Check tool_cache first
    # Execute tool, measure latency
    # Write latency to orch_metrics
    # Store result in tool_cache
    # Return result
```

**OpenAI function calling schemas** for all 8 tools — define in `registry.py` as `TOOL_SCHEMAS: list[dict]`.

**Verify**: Call `read_file("service/main.py")` via registry. Confirm content returned. Call `write_file("service/test_temp.py", "x=1")`, confirm file created, then delete it.

---

### Phase 11 — Prompt Builder
**Status**: ✅ COMPLETE

Create versioned prompt files in `orchestrator/prompts/`:
- One `.txt` file per stage (9 stages + classifier)
- Each file contains the system prompt for that stage
- First line format: `# version: {stage_name}_v1`

**`orchestrator/prompt_builder/loader.py`** — `PromptLoader`:
```python
def load(stage_name: str) -> tuple[str, str]:
    # Read orchestrator/prompts/{stage_name}_v1.txt (or latest version)
    # Return (prompt_text, version_string)
    # e.g. ("You are an expert...", "architecture_v1")
```

**`orchestrator/prompt_builder/builder.py`** — `PromptBuilder`:
```python
def build(
    stage_name: str,
    context: RunContext,
    memory_store: MemoryStore,
    conversation_history: list[dict],
    tool_results: list[dict],
) -> tuple[list[dict], str]:
    # Returns (messages, prompt_version)
    # messages = OpenAI messages format
    # Layer 1: system prompt (from loader)
    # Layer 2: codebase context (read key files via filesystem.read_file)
    # Layer 3: memory (from memory_store.format_for_prompt())
    # Layer 4: cross-stage context (from context.stage_artifacts)
    # Layer 5: conversation_history
    # Layer 6: tool_results
    # Layer 7: stage-specific instruction (built from context.resolved_requirement)
```

**Codebase context** (Layer 2) — files to read per stage:
```python
STAGE_CONTEXT_FILES = {
    "architecture_design": [
        "service/api/v1/router.py",
        "service/main.py",
        "service/config.py",
        "service/models/__init__.py",
    ],
    "implementation": [
        # set from implementation_plan artifact in RunContext
    ],
    # etc.
}
```

**Verify**: Call `builder.build("architecture_design", context, ...)`. Confirm output is a valid OpenAI messages list with all 7 layers present.

---

### Phase 12 — Stage Agent
**Status**: ❌ TODO

File: **`orchestrator/agents/stage_agent.py`** — `StageAgent`:
```python
from langsmith import traceable

@traceable(name="stage_agent.run")
def run(
    stage_name: str,
    state: OrchestratorState,
    gateway: AIGateway,
    memory_store: MemoryStore,
    tool_registry: ToolRegistry,
    response_cache: ResponseCache,
) -> StageResult:
    # 1. Build prompt via PromptBuilder
    # 2. Check response cache
    # 3. If cache miss: call gateway.call()
    # 4. Handle tool_calls in response (multi-turn conversation loop)
    # 5. Continue until no more tool calls (final structured output)
    # 6. Parse final output → StageResult.output_artifact
    # 7. Return StageResult
```

The `@traceable` decorator makes this function a named span in LangSmith. Every LLM call made via the gateway inside this function automatically becomes a child span, giving a full trace tree: `stage_agent.run` → `gateway.call` → OpenAI API call. No other instrumentation needed.

**Tool call handling** (multi-turn loop):
```python
while response has tool_calls:
    for tool_call in response.tool_calls:
        result = tool_registry.call(tool_call.name, tool_call.args, state, metrics)
        conversation_history.append({"role": "tool", "content": result, ...})
    response = gateway.call(updated_messages)
```

**Verify**: Mock the OpenAI response. Confirm tool calls are executed via registry and conversation continues until final answer.

---

### Phase 13 — Orchestration Engine
**Status**: ❌ TODO

> **LangGraph replaces the custom DAG loop.** The ~400-line execute() loop with manual threading, retry logic, and gate polling is replaced by a declarative ~80-line LangGraph StateGraph that provides all of these natively: parallel fan-out, `interrupt()` for human gates, `RetryPolicy` for retries, and `PostgresSaver` for state checkpointing.

File: **`orchestrator/core/engine.py`** — `OrchestrationEngine`:

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt

class OrchestrationEngine:
    def __init__(self, db_session, scenario):
        self.db_session = db_session
        self.scenario = scenario

    def build_graph(self) -> CompiledGraph:
        # Delegate to the scenario — each scenario defines its own graph topology
        saver = PostgresSaver(self.db_session)
        return self.scenario.build_graph(saver)

    def run(self, initial_state: OrchestratorState) -> OrchestratorState:
        graph = self.build_graph()
        config = {"configurable": {"thread_id": initial_state["run_id"]}}
        return graph.invoke(initial_state, config)

    def resume(self, run_id: str, human_input: dict) -> OrchestratorState:
        # Called after a human approves a gate (interrupt resumed)
        graph = self.build_graph()
        config = {"configurable": {"thread_id": run_id}}
        return graph.invoke(human_input, config)
```

**How LangGraph handles what the old engine did manually**:

| Old engine concern | LangGraph equivalent |
|---|---|
| `threading.Thread` for parallel stages | `add_edge([a, b], c)` — fan-out runs nodes concurrently |
| Manual sync point (wait for all threads) | `add_edge([a, b], c)` fan-in — c waits for both a and b |
| `while True: time.sleep(30)` PR polling | `interrupt(payload)` — state saved to DB; process exits cleanly |
| `for attempt in range(max_attempts)` retry loop | `RetryPolicy(max_attempts=3, backoff_factor=2.0)` on node |
| Crash recovery (re-read context from DB) | `PostgresSaver` checkpoints after every node; resume with same thread_id |
| `context.schema_change_detected` branch | `add_conditional_edges(node, fn)` — fn reads state, returns next node name |

**Per-node function signature** (same pattern for every stage):
```python
def requirements_analysis_node(state: OrchestratorState) -> dict:
    # Run stage agent
    result = stage_agent.run("requirements_analysis", state, ...)
    # Return ONLY the keys this node changed
    return {
        "stage_artifacts": {**state.get("stage_artifacts", {}), "requirements_analysis": result.output_artifact}
    }
```

**Verify**: Build a minimal 3-node graph (A → B → C) with mock node functions. Invoke it, confirm all three nodes execute in order and state is checkpointed to PostgreSQL after each node.

---

### Phase 14 — Planner
**Status**: ❌ TODO

**`orchestrator/planner/classifier.py`** — `RequirementClassifier`:
```python
def classify(requirement: str, gateway: AIGateway, token: str) -> str:
    # Single gpt-4o-mini call using classifier_v1.txt prompt
    # Returns "greenfield" | "brownfield" | "ambiguous"
    # Structured output via function calling
```

**`orchestrator/planner/planner.py`** — `Planner`:
```python
def plan(requirement: str, classifier_result: str, ...) -> OrchestratorState:
    # Select scenario class based on classifier_result (greenfield / brownfield / ambiguous)
    # For "ambiguous": run clarification loop first, get resolved_requirement
    # Create run record in DB (orch_runs)
    # Return initial OrchestratorState with run_id, requirement, scenario_type, triggered_by set
    # Engine.run() will call scenario.build_graph() and invoke it with this state
```

**Clarification loop** (for ambiguous):
```python
def run_clarification_loop(requirement: str, gateway: AIGateway, ...) -> dict:
    # 1. Read codebase + docs/design.md NFRs
    # 2. Generate 4 targeted questions via LLM
    # 3. Present questions to user via CLI
    # 4. Collect answers
    # 5. LLM maps answers to scope
    # 6. Surface 3 assumptions, ask [y/n] to confirm
    # 7. Save all decisions to orch_memory
    # 8. Return resolved_requirement dict
```

**Verify**: Run classifier with "Add QR code" → returns "greenfield". Run with "Make the service production-ready" → returns "ambiguous".

---

### Phase 15 — Scenarios
**Status**: ❌ TODO

> **LangGraph replaces build_dag().** Each scenario now returns a compiled LangGraph `StateGraph` instead of a `DAGGraph`. The graph topology — fan-out, fan-in, conditional edges, interrupt() gates — is declared once per scenario using LangGraph's API.

**`orchestrator/scenarios/base.py`** — `BaseScenario`:
```python
from langgraph.graph.graph import CompiledGraph
from langgraph.checkpoint.postgres import PostgresSaver

class BaseScenario:
    def build_graph(self, saver: PostgresSaver) -> CompiledGraph:
        raise NotImplementedError
```

**`orchestrator/scenarios/greenfield.py`** — `GreenFieldScenario(BaseScenario)`:
```python
def build_graph(self, saver: PostgresSaver) -> CompiledGraph:
    graph = StateGraph(OrchestratorState)

    # Nodes — each is a plain function (OrchestratorState) -> dict
    graph.add_node("requirements_analysis", requirements_analysis_node)
    graph.add_node("architecture_design",   architecture_design_node)
    graph.add_node("architecture_gate",     architecture_gate_node)   # interrupt()
    graph.add_node("implementation_plan",   implementation_plan_node)
    graph.add_node("test_plan",             test_plan_node)
    graph.add_node("implementation",        implementation_node)
    graph.add_node("schema_gate",           schema_gate_node)         # interrupt(), conditional
    graph.add_node("unit_tests",            unit_tests_node)
    graph.add_node("integration_tests",     integration_tests_node)
    graph.add_node("tests_gate",            tests_gate_node)          # interrupt()
    graph.add_node("documentation",         documentation_node)
    graph.add_node("pr_gate",               pr_gate_node)             # interrupt()
    graph.add_node("release_readiness",     release_readiness_node)
    graph.add_node("release_gate",          release_gate_node)        # interrupt()

    # Edges
    graph.add_edge(START,                       "requirements_analysis")
    graph.add_edge("requirements_analysis",     "architecture_design")
    graph.add_edge("architecture_design",       "architecture_gate")
    # Fan-out: both run concurrently after gate approves
    graph.add_edge("architecture_gate",         "implementation_plan")
    graph.add_edge("architecture_gate",         "test_plan")
    # Fan-in: implementation waits for both
    graph.add_edge(["implementation_plan", "test_plan"], "implementation")
    # Conditional: schema gate only if schema_change_detected == True
    graph.add_conditional_edges(
        "implementation",
        lambda state: "schema_gate" if state.get("schema_change_detected") else "unit_tests",
    )
    graph.add_edge("schema_gate", "unit_tests")
    # Fan-out: both test types run concurrently
    graph.add_edge("unit_tests",                "tests_gate")
    graph.add_edge("integration_tests",         "tests_gate")
    graph.add_edge("tests_gate",                "documentation")
    graph.add_edge("documentation",             "pr_gate")
    graph.add_edge("pr_gate",                   "release_readiness")
    graph.add_edge("release_readiness",         "release_gate")
    graph.add_edge("release_gate",              END)

    return graph.compile(checkpointer=saver)
```

**`orchestrator/scenarios/brownfield.py`** — `BrownfieldScenario(BaseScenario)`:
- Same graph topology as GreenField
- Difference: `requirements_analysis_node` and `architecture_design_node` use brownfield-specific prompts that instruct the agent to read existing code before proposing changes

**`orchestrator/scenarios/ambiguous.py`** — `AmbiguousScenario(BaseScenario)`:
- Same graph topology
- Difference: Planner runs clarification loop BEFORE graph.invoke(), sets `resolved_requirement` in initial `OrchestratorState`
- First node reads `state["resolved_requirement"]` instead of raw `state["requirement"]`

**Note on Gate #2 (schema change)**: `add_conditional_edges` reads `state["schema_change_detected"]` to decide whether to route to `schema_gate` or skip directly to `unit_tests`. The implementation node must set `schema_change_detected: True` in its returned dict if it detects a DB schema change.

**Verify**: Build the GreenField graph with mock node functions. Invoke it with a minimal OrchestratorState. Confirm all nodes execute in the correct order and that fan-out nodes run concurrently (check via timestamps in stage_artifacts).

---

### Phase 16 — Metrics Tracker
**Status**: ❌ TODO

> **LangSmith handles all LLM-level tracing automatically** (inputs, outputs, token counts, cost, latency per call). The `MetricsTracker` here only needs to aggregate our `orch_metrics` table — which exists for a different purpose: `TokenBudgetManager` queries it in real time to enforce per-role daily token caps. `MetricsTracker` reads those same rows to produce the end-of-run CLI summary.

File: **`orchestrator/metrics/tracker.py`** — `MetricsTracker`:
```python
def summarize(run_id: str) -> dict:
    # Aggregate from orch_metrics for this run:
    # total_cost_usd, total_tokens, cache_hit_rate,
    # avg_llm_latency_ms, avg_tool_latency_ms,
    # stages_completed, stages_failed, retry_count

def compute_mttr(run_id: str) -> float | None:
    # Average time from STAGE_FAILED to next STAGE_COMPLETED (retries)
    # Returns None if no retries occurred

def success_rate() -> float:
    # count(status='completed') / count(*) from orch_runs
```

**Verify**: After running a test orchestration, call `summarize(run_id)`. Confirm all fields populated.

---

### Phase 17 — CLI Entry Point
**Status**: ❌ TODO

File: **`orchestrator/run.py`**

```python
# python -m orchestrator.run "Add QR code endpoint" --token alice_dev_token
# python -m orchestrator.run approve --run-id orch-abc-123 --gate architecture --token bob_tl_token

import argparse

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    # Run command
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("requirement", type=str)
    run_parser.add_argument("--token", required=True)

    # Approve command
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--run-id", required=True)
    approve_parser.add_argument("--gate", required=True,
                                choices=["architecture", "schema_change", "release"])
    approve_parser.add_argument("--token", required=True)

    args = parser.parse_args()

    if args.command == "run":
        handle_run(args.requirement, args.token)
    elif args.command == "approve":
        handle_approve(args.run_id, args.gate, args.token)

def handle_run(requirement: str, token: str):
    # 1. Gateway pre-flight (auth, authz, rate limit, input validation)
    # 2. Memory.seed_if_empty()
    # 3. Planner.classify() → Planner.plan()
    # 4. Engine.execute(dag, context, ...)
    # 5. Print run summary
    # 6. Prompt for user feedback [1-4]
    # 7. state_store.update_run_completed(feedback)

def handle_approve(run_id: str, gate: str, token: str):
    # RBACCheckpoint.request_approval(run_id, gate, ..., token)
    # Print result
```

**Verify**:
```bash
# Dry-run without OpenAI (mock the agent):
python -m orchestrator.run "test requirement" --token alice_dev_token
```

---

## End-to-End Test Checklist

After all phases complete, verify with these smoke tests:

```bash
# 1. Migrations applied
alembic current
# Should show two migrations: url shortener tables + orch_ tables

# 2. Greenfield scenario
python -m orchestrator.run \
  "Add QR code endpoint GET /api/v1/urls/{id}/qr" \
  --token alice_dev_token

# 3. In a second terminal: approve architecture gate as Bob
python -m orchestrator.run approve \
  --run-id <from above> \
  --gate architecture \
  --token bob_tl_token

# 4. Continue approving remaining gates

# 5. Check audit log
psql $DATABASE_URL -c "SELECT event_type, actor, details FROM orch_audit_events WHERE run_id='...' ORDER BY created_at;"

# 6. Check metrics
psql $DATABASE_URL -c "SELECT stage_name, tokens_in, tokens_out, cost_usd, cache_hit FROM orch_metrics WHERE run_id='...';"

# 7. Check memory was updated
psql $DATABASE_URL -c "SELECT memory_type, actor, content FROM orch_memory ORDER BY created_at;"
```

---

## Environment Variables Required

Add these to `.env`:
```
OPENAI_API_KEY=sk-...
GITHUB_TOKEN=ghp_...          # service account PAT, repo scope
GITHUB_REPO=agentic-dev-projects/url-copilot
LANGCHAIN_TRACING_V2=true     # enables LangSmith automatic LLM tracing
LANGCHAIN_API_KEY=lsv2_...    # LangSmith API key
LANGCHAIN_PROJECT=url-copilot # LangSmith project name
```

---

## Phase Completion Checklist

| Phase | Description | Status |
|---|---|---|
| 1 | Config files + directory skeleton | ✅ |
| 2 | DB migration for orch_ tables | ✅ |
| 3 | Core data models (stage.py, state.py — OrchestratorState TypedDict for LangGraph) | ✅ |
| 3.5 | Evaluator component (hybrid LLM-as-Judge) | ✅ |
| 4 | State store (RunStateStore — orch_runs + orch_stage_results) | ✅ |
| 5 | Audit logger (AuditLogger + EventType — append-only orch_audit_events) | ✅ |
| 6 | Gateway: auth + RBAC checkpoint (TokenAuthenticator, RBACCheckpoint, four-eyes) | ✅ |
| 7 | Gateway: full pipeline (11-step pipeline, wrap_openai LangSmith tracing) | ✅ |
| 8 | Memory system (MemoryStore — seed/save/invalidate/format_for_prompt) | ✅ |
| 9 | Cache (ResponseCache 24h TTL + ToolCache in-memory per-run) | ✅ |
| 10 | Tool registry (filesystem, test_runner, github_client + OpenAI schemas) | ✅ |
| 11 | Prompt builder (7-layer assembly, PromptLoader latest-version resolution) | ✅ |
| 12 | Stage agent | ❌ |
| 13 | Orchestration engine | ❌ |
| 14 | Planner + clarification loop | ❌ |
| 15 | Scenarios (greenfield, brownfield, ambiguous) | ❌ |
| 16 | Metrics tracker | ❌ |
| 17 | CLI entry point | ❌ |

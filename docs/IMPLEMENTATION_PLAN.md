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
   orch_users      (standalone)
   orch_runs       (standalone)
   orch_stage_results  (FK → orch_runs)
   orch_audit_events   (FK → orch_runs)
   orch_metrics        (FK → orch_runs)
   orch_memory         (FK → orch_runs, nullable)
   orch_cache      (standalone)
   ```

   The `downgrade()` function drops in reverse order.

3. Apply migration:
   ```bash
   alembic upgrade head
   ```

4. Seed `orch_users` table with the 4 demo users from `config/users.yaml`:
   ```sql
   INSERT INTO orch_users VALUES
     ('alice', 'alice@example.com', 'DEVELOPER', true, now()),
     ('bob', 'bob@example.com', 'TECH_LEAD', true, now()),
     ('carol', 'carol@example.com', 'RELEASE_MANAGER', true, now()),
     ('dave', 'dave@example.com', 'ADMIN', true, now());
   ```

**Verify**: `alembic current` shows two migrations applied. All 7 `orch_` tables exist in PostgreSQL.

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

**`orchestrator/core/context.py`**:
```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class RunContext:
    run_id: str
    requirement: str
    resolved_requirement: str = ""
    scenario_type: str = ""
    triggered_by: str = ""              # github_login
    stage_artifacts: dict[str, Any] = field(default_factory=dict)
    tool_cache: dict[str, Any] = field(default_factory=dict)
    feature_branch: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    schema_change_detected: bool = False
    assumptions: list[str] = field(default_factory=list)
    stage_evaluations: dict[str, Any] = field(default_factory=dict)  # keyed by stage_name → HybridFeedback (typed in Phase 3.5)
```

**`orchestrator/core/dag.py`**:

Implement `DAGGraph` with:
- `add_node(node: StageNode) -> None`
- `add_edge(from_stage: str, to_stage: str) -> None`
- `get_ready_stages() -> list[StageNode]` — nodes whose all deps are COMPLETED
- `mark_completed(stage_name: str) -> None`
- `mark_failed(stage_name: str) -> None`
- `is_complete() -> bool` — all nodes COMPLETED or SKIPPED
- `is_stuck() -> bool` — some nodes FAILED, no RUNNING nodes, not complete
- `topological_sort() -> list[str]` — for display/logging purposes

**Verify**:
```python
# Quick smoke test (no pytest needed)
dag = DAGGraph()
dag.add_node(StageNode("A"))
dag.add_node(StageNode("B", depends_on=["A"]))
dag.add_node(StageNode("C", depends_on=["A"]))
dag.add_node(StageNode("D", depends_on=["B", "C"]))
assert [n.name for n in dag.get_ready_stages()] == ["A"]
dag.mark_completed("A")
ready = {n.name for n in dag.get_ready_stages()}
assert ready == {"B", "C"}   # parallel
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
**Status**: ❌ TODO
**PostgreSQL reads and writes for all orch_ tables. Uses existing `service.db.session.SessionLocal`.**

File: **`orchestrator/state/store.py`**

Implement `RunStateStore` with these methods:
```python
def create_run(run_id, requirement, scenario_type, triggered_by) -> None
def update_run_status(run_id, status) -> None
def update_run_completed(run_id, feedback_score, feedback_comment) -> None
def save_stage_result(result: StageResult, run_id: str) -> None
def get_run(run_id: str) -> dict
def get_stage_result(run_id: str, stage_name: str) -> dict | None
def load_run_context(run_id: str) -> RunContext
```

Uses raw SQL or SQLAlchemy Core (no new ORM models needed — tables were created in Phase 2 as raw DDL).

**Verify**: Write a test that creates a run record, saves a stage result, reads it back.

---

### Phase 5 — Governance: Audit Logger
**Status**: ❌ TODO

File: **`orchestrator/governance/audit.py`**

Implement `AuditLogger` with:
```python
def log(run_id, event_type, stage_name=None, actor="system", actor_role=None, details=None) -> None
```

Writes to `orch_audit_events` table. Uses `RunStateStore` internally.
**Constraint**: This method only ever INSERTs — never UPDATEs existing rows.

Event type constants (define as string literals or Enum):
```
STAGE_STARTED, STAGE_COMPLETED, STAGE_FAILED, STAGE_RETRYING
CHECKPOINT_REACHED, CHECKPOINT_APPROVED, CHECKPOINT_REJECTED, CHECKPOINT_APPROVED_OVERRIDE
EVALUATOR_STARTED, EVALUATOR_COMPLETED
RUN_STARTED, RUN_COMPLETED, RUN_FAILED
PR_CREATED, PR_MERGED
MEMORY_WRITTEN
CLARIFICATION_ASKED, CLARIFICATION_ANSWERED
```

**Verify**: Log 3 events for a run, query DB, confirm 3 rows in correct order.

---

### Phase 6 — Gateway: Auth and RBAC
**Status**: ❌ TODO
**Start with auth — it is the entry point for all CLI commands.**

**`orchestrator/gateway/auth.py`** — `TokenAuthenticator`:
```python
def resolve(token: str) -> CurrentUser:
    # Load orchestrator/config/users.yaml
    # Look up token key → return CurrentUser dataclass
    # Raise AuthenticationError if token not found
```

**`CurrentUser` dataclass** (define in `gateway/auth.py`):
```python
@dataclass
class CurrentUser:
    github_login: str
    email: str
    role: str
    permissions: list[str]
    daily_token_budget: int  # -1 = unlimited
```

**`orchestrator/governance/checkpoint.py`** — `RBACCheckpoint`:
```python
def check_permission(user: CurrentUser, permission: str) -> None:
    # Raise AuthorizationError if user.permissions does not contain permission
    # Permissions are inherited: ADMIN has all, RELEASE_MANAGER has ADMIN's minus manage_users, etc.

def request_approval(
    run_id: str,
    gate_name: str,
    required_permission: str,
    run_triggered_by: str,
    approver_token: str
) -> str:  # returns comment or ""
    # 1. Resolve approver_token → CurrentUser
    # 2. check_permission(approver, required_permission)
    # 3. Four-eyes: approver.github_login != run_triggered_by
    # 4. Show gate summary, prompt [y/n]
    # 5. If approved: audit.log(CHECKPOINT_APPROVED), return comment
    # 6. If rejected: audit.log(CHECKPOINT_REJECTED), raise ApprovalRejectedError
```

Load permissions from `orchestrator/config/rbac.yaml`. Respect `inherits` field to build full permission list per role.

**Verify**: Test that `alice` (DEVELOPER) cannot call `check_permission(alice, "approve_architecture")` without raising. Test that `bob` (TECH_LEAD) can.

---

### Phase 7 — Gateway: Full Pipeline
**Status**: ❌ TODO
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
**Status**: ❌ TODO

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
**Status**: ❌ TODO

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
**Status**: ❌ TODO

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
**Status**: ❌ TODO

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
def run(
    stage_name: str,
    context: RunContext,
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

**Tool call handling** (multi-turn loop):
```python
while response has tool_calls:
    for tool_call in response.tool_calls:
        result = tool_registry.call(tool_call.name, tool_call.args, context, metrics)
        conversation_history.append({"role": "tool", "content": result, ...})
    response = gateway.call(updated_messages)
```

**Verify**: Mock the OpenAI response. Confirm tool calls are executed via registry and conversation continues until final answer.

---

### Phase 13 — Orchestration Engine
**Status**: ❌ TODO

File: **`orchestrator/core/engine.py`** — `OrchestrationEngine`:

```python
def execute(
    dag: DAGGraph,
    context: RunContext,
    gateway: AIGateway,
    checkpoint: RBACCheckpoint,
    audit: AuditLogger,
    state_store: RunStateStore,
    memory_store: MemoryStore,
    tool_registry: ToolRegistry,
) -> None:
    # Main loop:
    while not dag.is_complete():
        ready_stages = dag.get_ready_stages()

        # Run parallel-ready stages concurrently
        # Use threading.Thread or asyncio depending on implementation
        for stage in ready_stages:
            run_stage(stage, context, ...)

        # Wait for all concurrent stages to complete (sync point)
        # If dag.is_stuck(): handle failure (pause run, notify)
```

**Per-stage execution**:
```python
def run_stage(stage, context, ...):
    for attempt in range(1, stage.max_attempts + 1):
        audit.log(run_id, STAGE_STARTED, stage.name)
        try:
            result = stage_agent.run(stage.name, context, ...)
            # Exit gate: guardrail scan on output
            guardrail.check_output(result.output_artifact)
            # If gate required: request approval
            if stage.requires_gate:
                approve_gate(stage, context, checkpoint, audit)
            dag.mark_completed(stage.name)
            context.stage_artifacts[stage.name] = result.output_artifact
            state_store.save_stage_result(result, context.run_id)
            audit.log(run_id, STAGE_COMPLETED, stage.name)
            return
        except Exception as e:
            audit.log(run_id, STAGE_RETRYING if attempt < max else STAGE_FAILED, ...)
            if attempt == stage.max_attempts:
                dag.mark_failed(stage.name)
                raise
```

**GitHub PR gate** (between DOCUMENTATION and RELEASE_READINESS):
```python
def wait_for_pr_merge(context, tool_registry, audit):
    pr_number, pr_url = tool_registry.call("create_pr", {...}, context, ...)
    context.pr_url = pr_url
    context.pr_number = pr_number
    audit.log(run_id, PR_CREATED, details={"pr_url": pr_url})
    print(f"\nPR created: {pr_url}")
    print("Waiting for TECH_LEAD to review and merge...")
    while True:
        status = tool_registry.call("poll_pr_status", {"pr_number": pr_number}, ...)
        if status["merged"]:
            audit.log(run_id, PR_MERGED, actor=status["merged_by"])
            return
        if status["closed"]:
            raise PRRejectedError("PR closed without merging")
        time.sleep(30)
```

**Verify**: Build a minimal 3-stage DAG (A → B → C), run engine with mock agents, confirm all stages execute in order with audit events written.

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
def plan(requirement: str, classifier_result: str, ...) -> tuple[DAGGraph, RunContext]:
    # Select scenario DAG based on classifier_result
    # For "ambiguous": run clarification loop first, get resolved_requirement
    # Create run record in DB
    # Return (dag, context)
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

**`orchestrator/scenarios/base.py`** — `BaseScenario`:
```python
class BaseScenario:
    def build_dag(self, context: RunContext) -> DAGGraph:
        raise NotImplementedError
```

**`orchestrator/scenarios/greenfield.py`** — `GreenfieldScenario(BaseScenario)`:
```python
def build_dag(self, context: RunContext) -> DAGGraph:
    dag = DAGGraph()
    dag.add_node(StageNode("requirements_analysis"))
    dag.add_node(StageNode("architecture_design",
                           depends_on=["requirements_analysis"],
                           requires_gate="approve_architecture"))
    dag.add_node(StageNode("implementation_plan", depends_on=["architecture_design"]))
    dag.add_node(StageNode("test_plan", depends_on=["architecture_design"]))
    dag.add_node(StageNode("implementation",
                           depends_on=["implementation_plan", "test_plan"],
                           requires_gate="approve_schema_change"))  # conditional
    dag.add_node(StageNode("unit_tests", depends_on=["implementation"]))
    dag.add_node(StageNode("integration_tests", depends_on=["implementation"]))
    dag.add_node(StageNode("documentation", depends_on=["unit_tests", "integration_tests"]))
    dag.add_node(StageNode("release_readiness",
                           depends_on=["documentation"],
                           requires_gate="approve_release"))
    return dag
```

**`orchestrator/scenarios/brownfield.py`** — `BrownfieldScenario(BaseScenario)`:
- Same DAG structure as Greenfield
- Difference: REQUIREMENTS_ANALYSIS prompt instructs agent to read existing code first
- ARCHITECTURE_DESIGN prompt includes impact analysis instruction

**`orchestrator/scenarios/ambiguous.py`** — `AmbiguousScenario(BaseScenario)`:
- Same DAG structure
- Difference: Planner runs clarification loop BEFORE building DAG
- `resolved_requirement` set in `RunContext` before first stage executes

**Note on Gate #2 (schema change)**: The `approve_schema_change` gate is conditional.
In `engine.run_stage()`, check `context.schema_change_detected` before requesting approval.
The implementation stage's output artifact must include `"schema_change": bool`.
If `True`, set `context.schema_change_detected = True` and trigger gate.

---

### Phase 16 — Metrics Tracker
**Status**: ❌ TODO

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
```

---

## Phase Completion Checklist

| Phase | Description | Status |
|---|---|---|
| 1 | Config files + directory skeleton | ❌ |
| 2 | DB migration for orch_ tables | ❌ |
| 3 | Core data models (stage, dag, context) | ❌ |
| 3.5 | Evaluator component (hybrid LLM-as-Judge) | ❌ |
| 4 | State store (PostgreSQL reads/writes) | ❌ |
| 5 | Audit logger | ❌ |
| 6 | Gateway: auth + RBAC checkpoint | ❌ |
| 7 | Gateway: full pipeline | ❌ |
| 8 | Memory system | ❌ |
| 9 | Cache (response + tool) | ❌ |
| 10 | Tool registry | ❌ |
| 11 | Prompt builder | ❌ |
| 12 | Stage agent | ❌ |
| 13 | Orchestration engine | ❌ |
| 14 | Planner + clarification loop | ❌ |
| 15 | Scenarios (greenfield, brownfield, ambiguous) | ❌ |
| 16 | Metrics tracker | ❌ |
| 17 | CLI entry point | ❌ |

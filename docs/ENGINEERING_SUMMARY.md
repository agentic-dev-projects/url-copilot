# Final Engineering Summary — url-copilot

---

## What Was Built

**url-copilot** is two systems in one repository:

1. **A production-quality URL shortener** (FastAPI + PostgreSQL + Redis) with authentication, analytics, rate limiting, and soft-delete. 41 tests passing.

2. **An AI SDLC orchestrator** that accepts a natural-language requirement, classifies it, decomposes it into a 9-stage LangGraph pipeline, executes each stage using GPT-4o, enforces human approval at four gates with RBAC, writes actual code to GitHub, and produces a full audit trail — all with controlled autonomy.

---

## Artifacts

| Artifact | Location |
|---|---|
| URL shortener service | `service/` |
| AI orchestration system | `orchestrator/` |
| 41 service tests (unit + integration) | `service/tests/` |
| Orchestrator unit + integration tests | `orchestrator/tests/` |
| Architecture design doc | `docs/orchestrator-architecture.md` |
| URL shortener design doc | `docs/design.md` |
| Gate reference (RBAC, four-eyes rule) | `docs/GATES.md` |
| Quick start + CLI commands | `docs/QUICK_START.md` |
| Testing strategy + what is mocked | `docs/TESTING.md` |
| Greenfield scenario walkthrough | `docs/scenarios/greenfield.md` |
| Brownfield scenario walkthrough | `docs/scenarios/brownfield.md` |
| Ambiguous scenario walkthrough | `docs/scenarios/ambiguous.md` |
| Roadmap (future improvements) | `docs/ROADMAP.md` |

All three scenarios were validated with real LLM runs. GitHub PRs were created at:
- PR #4 — Brownfield: Add pagination to GET /api/v1/urls (orch-a82feb76)
- PR #5 — Greenfield: QR code endpoint (separate run)

---

## Plan and Rationale

### Why LangGraph

LangGraph was chosen over a hand-rolled state machine for three reasons:
1. **Native parallel fan-out with synchronization** — `implementation_plan` + `test_plan` run concurrently; `unit_tests` + `integration_tests` run concurrently. LangGraph handles the fan-in sync automatically.
2. **Built-in `interrupt()` for human gates** — pausing execution at an approval checkpoint and resuming with `Command(resume=...)` is first-class. Implementing this with a custom state machine adds ~400 lines of concurrency and persistence code.
3. **PostgresSaver checkpointing** — the entire `OrchestratorState` is serialized to PostgreSQL after every node. A crashed or gate-paused run resumes from exactly where it stopped, across process restarts.

### Why OpenAI GPT-4o for implementation stages

Function calling with structured JSON output is the only reliable way to get deterministic, parseable artifacts from an LLM. GPT-4o's function-calling quality is materially better than GPT-4o-mini for implementation and architecture stages where code correctness matters. Cheaper stages (requirements analysis, documentation) use GPT-4o-mini.

### Why four gates and not fewer

The four-gate structure maps to real enterprise change-control requirements:
- **architecture_gate**: validates design before any code is written (cheapest point to reject)
- **tests_gate**: validates coverage before code review (catches prompt-fabricated test results)
- **pr_gate**: code review checkpoint with a real GitHub PR link
- **release_gate**: final sign-off with a standardized release checklist

Collapsing to fewer gates would skip meaningful review points. The four-eyes rule (submitter ≠ approver) satisfies SOX/SOC2 separation-of-duties requirements.

### Why YAML-based auth (not JWT or OAuth)

The prototype uses static tokens in `users.yaml` to keep the demo self-contained. Replacing `TokenAuthenticator.resolve(token)` with a GitHub OAuth call is a one-function change — all RBAC, four-eyes, and audit logic stays unchanged. The interface was designed with this swap in mind.

---

## Key Design Decisions and Trade-offs

| Decision | Choice | Rationale | Trade-off |
|---|---|---|---|
| LLM | GPT-4o | Best function-calling quality for code stages | Vendor lock-in; ~$0.30–$0.50 per run |
| Cheap stages use GPT-4o-mini | Yes | Classification, docs, test plans — templated tasks | Slight quality reduction |
| State backend | PostgreSQL (existing) | Reuses existing infrastructure; ACID guarantees | Orchestrator requires DB to run |
| Auth | Token + YAML | Self-contained prototype; swappable to real identity | Not production-safe; tokens in plain text |
| Human approval | CLI [y/n] | Works everywhere; gate *location* matters more than mechanism | Not async; approver must be present |
| Code review gate | GitHub PR | Integrates with existing workflow | Requires GitHub API token |
| Four-eyes | Trigger user ≠ approver | SOX/change-control requirement | Increases friction |
| Artifact scope | Actual code on GitHub | Demonstrates real autonomous capability | Wide security surface; strong guardrails required |
| Parallel stages | Yes | Reduces wall-clock time by ~40% | Slightly more complex engine logic |
| LLM observability | LangSmith (automatic) | Zero-instrumentation tracing | External SaaS; not suitable for real-time budget enforcement |
| Budget enforcement | Local PostgreSQL query | Sub-millisecond; no network dependency per call | Duplicate of LangSmith token counts |

---

## Validation and Risk Management

### How the system validates its own outputs

1. **Schema validation**: Every stage agent output is parsed against a typed JSON schema. Malformed or incomplete LLM responses raise a validation error that triggers a retry.
2. **Test execution**: The `implementation` stage calls `run_tests` before committing. A test failure is surfaced to the gate reviewer, not silently passed through.
3. **Prompt hardening against fabrication**: The `unit_tests_v1.txt` and `integration_tests_v1.txt` prompts include an explicit CRITICAL section requiring the LLM to copy exact values from `run_tests` output rather than copying template values. This was added after observing a real run where the LLM reported `success: true` while copying a hardcoded template.
4. **Implementation prompt fix**: `implementation_v1.txt` Phase 4 was rewritten to require fixing SyntaxError/ImportError before proceeding to commit. The original "proceed regardless" instruction was a loophole the LLM exploited to skip `commit_and_push` when it detected its own syntax error.
5. **Release readiness checklist**: `release_readiness` stage runs 10 checks (tests pass, auth enforced, no debug code, no hardcoded secrets, soft-delete followed, dependencies declared, error handling present) before the final gate.
6. **Output guardrails**: Post-call response is scanned for dangerous patterns (`os.system`, `subprocess`, `rm -rf`, `DROP TABLE`, hardcoded passwords) before being acted on.

### Security risks (prototype scope)

| Risk | Severity | Mitigation in Prototype | Production Fix |
|---|---|---|---|
| SSRF via stored URLs | High | `http`/`https` scheme check at creation | Add private IP denylist (`ipaddress` module) |
| Open user registration | Medium | No real users; dev-only | Add rate limiting + invite/allowlist |
| Auth token in LangGraph checkpoint | Medium | Only test tokens; no prod data | Remove `token` from `OrchestratorState`; re-supply at resume time |
| LLM writes arbitrary Python to `service/` | High | Write restricted to `service/`; guardrails scan output | Add content hash verification between gate review and commit |
| IP hash without salt | Medium | SHA-256 of IP for analytics only | HMAC with server secret |
| Non-atomic `click_count` increment | Low | Lost updates under concurrency | `UPDATE ... SET click_count = click_count + 1` (atomic SQL) |
| `psycopg2` vs `psycopg` in requirements.txt | High | Fixed | Already patched |
| `flake8` missing from requirements.txt | Medium | Fixed | Already patched |

---

## Assumptions

1. **Single-node deployment** — the orchestrator is designed for CLI use by one operator at a time. Concurrent multi-user pipeline execution requires connection pool separation and distributed locking on the LangGraph checkpoint.
2. **Service and orchestrator share a PostgreSQL instance** — co-location is intentional for prototype simplicity. A production deployment would isolate orchestrator tables in a separate schema or database.
3. **Local filesystem is the staging area** — the implementation stage writes files to `service/` on the local machine, then pushes to GitHub. This means the orchestrator must run on a machine with the repo cloned and GitHub credentials configured.
4. **Linear run lifecycle** — each run progresses forward through gates. There is no mechanism to re-open a completed gate or restart from a mid-point without creating a new run.
5. **GitHub as the merge target** — the system creates feature branches and PRs but does not auto-merge. A human must merge the PR on GitHub. The `pr_gate` approves the PR creation, not the merge.

---

## Limitations

### Implemented in design, not yet wired in code

The following were designed and documented in `docs/orchestrator-architecture.md` and `orchestrator/config/rbac.yaml` but are not connected to the CLI or pipeline:

| Feature | Design Location | Implementation Status |
|---|---|---|
| **Bounded retries per stage** | Section 10 of orchestrator-architecture.md | LangGraph `RetryPolicy` is not configured on any node. A failed stage either succeeds on re-run or terminates the run. |
| **Force-stop command** | `force_stop` permission in rbac.yaml | No `--force-stop` CLI command exists. A paused run can only be rejected at the next gate, not stopped immediately. |
| **Rollback** | `rollback` permission in rbac.yaml | No rollback method is implemented. Once a stage is approved, the decision cannot be reversed without starting a new run. |
| **Dynamic re-planning** | Section 10 of orchestrator-architecture.md | When an architecture gate is rejected, the run stops. It does not re-run upstream stages with a revised design and propagate new context to downstream stages. A new `run` command must be submitted. |

### End-to-end latency metric

Wall-clock run duration is computed (`time.perf_counter()`) and logged to stdout, but is not persisted to `orch_metrics` or included in the `summarize()` output. The per-stage metrics table shows LLM call latency per stage; total pipeline latency requires summing these or reading the `orch_runs.created_at` / `completed_at` timestamps.

### Orchestrator test coverage

The orchestrator unit tests cover the gateway, planner, and governance components. The full pipeline (LangGraph topology, stage ordering, gate pause/resume) is only tested via live E2E tests (`RUN_E2E=1`) that make real OpenAI API calls. There are no orchestrator integration tests that mock the LLM and verify the pipeline topology end-to-end without cost.

### Ambiguous scenario classifier routing

The classifier (`classifier_v1.txt`) is sensitive to requirement phrasing. Requirements with a clear action verb ("add", "modify", "extend") tend to route to greenfield or brownfield with high confidence even when the scope is genuinely ambiguous. The ambiguous classification fires most reliably for requirements with undefined terms or missing context (e.g., "make it enterprise-friendly", "add team support").

---

## What "Production-Ready" Would Add

See [ROADMAP.md](ROADMAP.md) for the full list. The highest-priority items from a production standpoint:

1. **Wire bounded retries** — configure `RetryPolicy(max_attempts=3)` on every LangGraph node (30-minute task)
2. **Force-stop CLI command** — add `orchestrator.run stop --run-id` that sets `orch_runs.status = 'stopped'` and skips the pending gate (1-hour task)
3. **Atomic click counter** — replace `click_count += 1` with `UPDATE ... SET click_count = click_count + 1` (15-minute task)
4. **SSRF protection** — add private IP denylist to `is_valid_url` (1-hour task)
5. **Remove auth token from state** — re-supply at resume time from CLI `--token` (30-minute task)
6. **Replace YAML auth with GitHub OAuth** — one method change in `TokenAuthenticator.resolve()` (2-hour task)
7. **React dashboard** — wrap `handle_run`, `handle_approve`, `handle_status` as FastAPI endpoints; build React frontend consuming them — see ROADMAP.md for migration path

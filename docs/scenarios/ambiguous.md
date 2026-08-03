# Scenario 3 — Ambiguous
## "Make the service production-ready"

**Scenario type**: Ambiguous — requirement has 7+ valid interpretations. The agent must NOT guess. It reads the codebase, maps gaps against existing NFRs, and asks targeted questions before building any plan.

**What this scenario demonstrates**:
- Ambiguity detection: agent identifies requirement is too broad before any SDLC stage runs
- Codebase-grounded questions: agent reads `docs/design.md` NFR section and maps what's done vs missing
- Targeted clarification loop: questions are specific and contextual — not generic "what do you mean?"
- Scope negotiation: agent proposes 3 focused changes from a potential list of 7
- Assumption documentation: every assumption is surfaced and confirmed before coding begins
- All assumptions saved to memory for future runs
- Dynamic DAG: requirements stage expands into a clarification sub-loop unique to this scenario type

---

## CLI Command

```bash
python -m orchestrator.run \
  "Make the service production-ready" \
  --token alice_dev_token
```

**Planner**:
```
Attempts classification: HIGH AMBIGUITY detected
  "production-ready" has no single technical definition
  Selects: AmbiguousScenario DAG
  Creates run: orch-ambig-003
  Enters: CLARIFICATION MODE (before any SDLC stage runs)
```

---

## DAG Execution Trace

```
CLARIFICATION LOOP  ← unique to ambiguous scenario
  ├── Codebase analysis
  ├── Question Round 1 (4 questions)
  ├── Scope narrowing
  └── Assumption confirmation
        │
        ▼ (requirements now unambiguous)
REQUIREMENTS_ANALYSIS         [COMPLETED]
        │
ARCHITECTURE_DESIGN           [COMPLETED → 🔐 Gate #1]
        │
        ├───────────────────────────────────────────────────┐
IMPLEMENTATION_PLAN [COMPLETED]              TEST_PLAN [COMPLETED]  (parallel)
        └──────────────────────┬────────────────────────────┘
                               │ sync point
                        IMPLEMENTATION           [COMPLETED → Gate #2 SKIPPED]
                               │
              ┌────────────────┴──────────────────┐
         UNIT_TESTS [COMPLETED]   INTEGRATION_TESTS [COMPLETED]  (parallel)
              └────────────────┬──────────────────┘
                               │ sync point
                        DOCUMENTATION            [COMPLETED]
                               │
                         🔐 GitHub PR → TECH_LEAD merges
                               │
                       RELEASE_READINESS         [COMPLETED → 🔐 Gate #4]
                               │
                          User Feedback
```

---

## Clarification Loop — Detailed Flow

### Step 1: Agent Reads Entire Codebase Against NFRs

Before asking a single question, the agent reads the codebase and maps it against
the existing NFRs documented in `docs/design.md` section 2.

**Tools called**:
```
read_file("docs/design.md")                       ← read NFR section (2.1-2.6)
read_file("service/main.py")                      ← check middleware
read_file("service/config.py")                    ← check settings
read_file("service/core/rate_limiter.py")         ← verify rate limiting
read_file("service/db/session.py")                ← verify connection pooling
search_codebase("logging")                        ← check logging setup
search_codebase("metrics")                        ← check metrics setup
search_codebase("request_id")                     ← check request ID propagation
search_codebase("CORSMiddleware")                 ← check CORS config
```

**Internal analysis**:
```
NFR Status Map:

  ✓ NFR-10: API keys hashed SHA-256, never plaintext          → DONE
  ✓ NFR-11: Input validation via Pydantic schemas             → DONE
  ✓ NFR-12: IP hashes for PII compliance in analytics        → DONE
  ✓ NFR-05: No data loss — soft deletes, no hard deletes      → DONE
  ✓ NFR-08: Horizontal scale — stateless handlers             → DONE
  ✓ NFR-09: Analytics decoupled from redirect path            → DONE (graceful degradation)
  ✓ NFR-17: Modular codebase — api/services/models layers    → DONE
  ✓ NFR-19: Rate limiting per API key                        → DONE
  ✓ NFR-20: Integration tests cover user journeys            → DONE (45 tests)

  ✗ NFR-14: Structured logs with request_id                   → NOT IMPLEMENTED
             (logging module not imported anywhere in main.py)
  ✗ NFR-15: Metrics endpoint (request rate, error rate)       → NOT IMPLEMENTED
             (no /metrics route, no instrumentation)
  ✗ NFR-16: Readiness probe                                   → PARTIAL
             (/health exists but returns static JSON, no DB/Redis check)
  ✗ NFR-01: Redirect p99 < 100ms                             → AT RISK
             (no Redis cache on redirect path)
  ✗ NFR-13: HTTPS enforced                                    → NOT ENFORCED
             (CORSMiddleware allow_origins=["*"] — no origin restriction)

  Additional gaps (not in NFR doc):
  - No CI/CD pipeline
  - No graceful shutdown handler
  - No readiness probe distinguishing DB-down vs app-down
```

### Step 2: Clarification Round 1 — Targeted Questions

The questions are derived directly from the gap analysis. The agent does NOT ask generic questions.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[orch-ambig-003] CLARIFICATION REQUIRED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
I analyzed the codebase against the NFRs defined in docs/design.md.

Already production-ready:
  ✓ Auth, rate limiting, validation, connection pooling,
    soft deletes, migration, 45 tests passing

Unmet NFRs I found (5 gaps, 2 additional):
  ✗ NFR-14: No structured JSON logging or request IDs
  ✗ NFR-15: No metrics endpoint
  ✗ NFR-16: Health check is liveness-only (no readiness)
  ✗ NFR-01: Redirect cache not yet built (p99 at risk)
  ✗ NFR-13: CORS allows all origins (security gap)
  ✗ Extra: No CI/CD pipeline
  ✗ Extra: No graceful shutdown

"Production-ready" maps to 7 different work items.
I need 4 answers to build a focused plan.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Q1. What is driving "production-ready" right now?
    [A] Upcoming compliance or security audit (SOC2, ISO 27001, PCI)
    [B] Going live — need operational confidence before launch
    [C] A specific incident or SLA breach drove this request
    [D] General engineering quality improvement, no specific pressure

Your answer: A
```

```
Q2. You selected: compliance audit.
    Which of these 7 gaps maps to your audit requirements?
    (I'll research the controls — just tell me the standard)

    [A] SOC2 Type II
    [B] ISO 27001
    [C] PCI DSS
    [D] Internal audit (not a public standard)
    [E] I'm not sure — pick what's most important

Your answer: A
```

```
Q3. Deployment target for this work?
    [A] Single server with Docker Compose (current setup)
    [B] Kubernetes
    [C] AWS ECS / Fargate
    [D] Not yet decided

Your answer: A
```

```
Q4. Deadline?
    Press Enter to skip.

Your answer: 2 weeks
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 3: Agent Maps Answers to SOC2 Controls

```
Mapping "SOC2 + 2 weeks + Docker Compose" to the 7 gaps:

SOC2 Trust Service Criteria relevant here:
  CC7.2 — System operations monitoring    → NFR-14 (structured logging) ← REQUIRED
  CC7.3 — Anomaly/failure detection      → NFR-15 (metrics endpoint)   ← REQUIRED
  CC6.1 — Logical access controls        → NFR-13 (CORS restriction)   ← REQUIRED
  CC6.6 — Security threats prevention   → NFR-14 (audit trail)        ← covered by logging

Not SOC2 blockers (defer beyond 2 weeks):
  ✗ Redis cache (NFR-01) — performance, not audit
  ✗ CI/CD pipeline — operational, not SOC2 control
  ✗ Graceful shutdown — reliability, not SOC2 control
  ✗ Readiness probe improvement — minor, not SOC2 control

Scope decision: 3 changes, all SOC2-motivated.
```

### Step 4: Scope Proposal and Assumption Confirmation

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scope confirmed for orch-ambig-003:

  1. Structured JSON logging + X-Request-ID header on all requests
     → Satisfies SOC2 CC7.2 (system monitoring)

  2. GET /metrics endpoint (request rate, error rate, latency p99)
     → Satisfies SOC2 CC7.3 (anomaly detection)

  3. CORS: restrict allowed origins via ALLOWED_ORIGINS env var
     → Satisfies SOC2 CC6.1 (access control)

Deferred (documented, not abandoned):
  - Redis cache on redirect (NFR-01) — separate backlog item
  - CI/CD pipeline — separate backlog item
  - Graceful shutdown — separate backlog item

Before I proceed, I need to confirm 3 assumptions:

  [A1] Request IDs: UUID4, added as X-Request-ID response header
  [A2] Metrics endpoint: unauthenticated (standard for Prometheus scraping)
  [A3] CORS: allowed origins set via comma-separated ALLOWED_ORIGINS env var
       e.g. "https://app.example.com,https://admin.example.com"

Do any assumptions need correction? [y/n]: n

✓ All assumptions confirmed by alice (DEVELOPER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Saving to memory:
  [fact]       SOC2 audit is the compliance driver for observability work
  [convention] Metrics endpoint must be unauthenticated for Prometheus scraping
  [convention] CORS allowed origins controlled via ALLOWED_ORIGINS env var
  [decision]   Request IDs: UUID4 as X-Request-ID response header

Proceeding to REQUIREMENTS_ANALYSIS with resolved requirements.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Stage 1 — REQUIREMENTS_ANALYSIS

Requirements are now unambiguous. This stage formalizes the clarification outputs.

**Output artifact**:
```json
{
  "original_requirement": "Make the service production-ready",
  "resolved_requirement": "Add structured JSON logging with X-Request-ID, a /metrics endpoint, and CORS restriction to ALLOWED_ORIGINS — all motivated by SOC2 audit (CC7.2, CC7.3, CC6.1)",
  "scope": [
    "Structured JSON logging + X-Request-ID header (NFR-14)",
    "GET /metrics endpoint, unauthenticated (NFR-15)",
    "CORS restricted to ALLOWED_ORIGINS env var (NFR-13)"
  ],
  "deferred": [
    "Redis cache on redirect path (NFR-01)",
    "CI/CD pipeline",
    "Graceful shutdown",
    "Readiness probe improvement"
  ],
  "assumptions": [
    "Request IDs: UUID4 as X-Request-ID header",
    "Metrics endpoint: unauthenticated",
    "CORS origins: ALLOWED_ORIGINS env var"
  ],
  "compliance_driver": "SOC2 CC7.2, CC7.3, CC6.1",
  "schema_migration": false
}
```

---

## Stage 2 — ARCHITECTURE_DESIGN

**Model**: gpt-4o
**Tools called**:
```
read_file("service/main.py")             ← middleware setup, router registration
read_file("service/api/deps.py")         ← request context pattern
read_file("service/config.py")           ← settings pattern
search_codebase("CORSMiddleware")        ← existing CORS config to modify
search_codebase("app.add_middleware")    ← middleware registration
```

**Output artifact**:
```json
{
  "change_1_logging": {
    "new_files": ["service/core/logging.py"],
    "modified_files": ["service/main.py"],
    "description": "JSON log formatter with request_id field. RequestIDMiddleware generates UUID4 per request, adds X-Request-ID to response headers, injects into log context."
  },
  "change_2_metrics": {
    "new_files": ["service/api/v1/endpoints/metrics.py"],
    "modified_files": ["service/api/v1/router.py", "requirements.txt"],
    "description": "prometheus_fastapi_instrumentator auto-instruments all routes. GET /metrics serves Prometheus scrape format. Unauthenticated (no X-API-Key required)."
  },
  "change_3_cors": {
    "modified_files": ["service/main.py", "service/config.py", ".env.example"],
    "description": "CORSMiddleware allow_origins reads from settings.allowed_origins (list parsed from ALLOWED_ORIGINS env var). Default: ['http://localhost:3000'] for local dev."
  },
  "schema_migration": false,
  "new_dependencies": ["prometheus_fastapi_instrumentator==7.0.0"]
}
```

### 🔐 Gate #1 — Architecture Approval

```
Approve? [y/n]: y
Comment: Good. Ensure request_id propagates into structured
         log fields — not just the response header. Also make
         sure ALLOWED_ORIGINS has a sane default for local dev.

✓ Approved by bob (TECH_LEAD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Memory saved:
  [preference] request_id must appear in structured log fields, not just response headers (bob)
  [convention] ALLOWED_ORIGINS must have a default for local development (bob)
```

---

## Stages 3a + 3b — IMPLEMENTATION_PLAN + TEST_PLAN (Parallel)

### Implementation Plan (3 changes as one coherent batch):
```json
{
  "tasks": [
    {"id": 1, "file": "service/core/logging.py", "action": "create",
     "description": "JSON log formatter factory, request_id context var"},
    {"id": 2, "file": "service/main.py", "action": "modify",
     "description": "Add RequestIDMiddleware, configure JSON logging on startup"},
    {"id": 3, "file": "service/api/v1/endpoints/metrics.py", "action": "create",
     "description": "Prometheus instrumentator, GET /metrics route (no auth)"},
    {"id": 4, "file": "service/api/v1/router.py", "action": "modify",
     "description": "Register metrics router"},
    {"id": 5, "file": "service/config.py", "action": "modify",
     "description": "Add ALLOWED_ORIGINS: list[str] = ['http://localhost:3000']"},
    {"id": 6, "file": "service/main.py", "action": "modify",
     "description": "Update CORSMiddleware to use settings.allowed_origins"},
    {"id": 7, "file": ".env.example", "action": "modify",
     "description": "Document ALLOWED_ORIGINS env var"},
    {"id": 8, "file": "requirements.txt", "action": "modify",
     "description": "Add prometheus_fastapi_instrumentator==7.0.0"}
  ]
}
```

### Test Plan:
```json
{
  "unit_tests": [
    "test_request_id_in_response_header: every response has X-Request-ID header",
    "test_request_ids_unique: two requests get different IDs",
    "test_request_id_in_log_output: log entry contains request_id field"
  ],
  "integration_tests": [
    "test_metrics_endpoint_returns_200: GET /metrics → 200 without API key",
    "test_metrics_endpoint_no_auth_required: GET /metrics without X-API-Key → 200 (not 401)",
    "test_metrics_contains_request_counter: response body contains http_requests_total",
    "test_cors_allows_configured_origin: request with ALLOWED_ORIGINS header → 200",
    "test_cors_blocks_unconfigured_origin: request with unknown origin → 403"
  ],
  "regression": "All 45 existing tests must still pass"
}
```

---

## Stage 4 — IMPLEMENTATION

**Model**: gpt-4o
**Branch**: `orch/feature/prod-ready-orch-ambig-003`

**Tools called**:
```
create_branch("orch/feature/prod-ready-orch-ambig-003")
read_file("service/main.py")
write_file("service/core/logging.py")            ← new JSON logger
write_file("service/main.py")                    ← middleware + logging
write_file("service/api/v1/endpoints/metrics.py") ← Prometheus endpoint
write_file("service/api/v1/router.py")           ← register metrics
write_file("service/config.py")                  ← ALLOWED_ORIGINS
write_file(".env.example")                       ← document ALLOWED_ORIGINS
write_file("requirements.txt")                   ← add instrumentator
run_linter()                                     → PASSED
```

No schema change → Gate #2 SKIPPED.

---

## Stages 5a + 5b — UNIT_TESTS + INTEGRATION_TESTS (Parallel)

```
New unit tests:        3/3  PASSED
New integration tests: 5/5  PASSED
Existing suite:       45/45 PASSED  ← full regression
Total:                53/53 PASSED
```

---

## Stage 6 — DOCUMENTATION

```
write_file("docs/design.md")
  ← Mark NFR-14, NFR-15, NFR-13 as IMPLEMENTED
  ← Document ALLOWED_ORIGINS config
write_file("README.md")
  ← Add curl example for GET /metrics
  ← Add ALLOWED_ORIGINS to environment variables section
```

---

## GitHub PR

```
PR #7
Title: feat: structured logging, metrics endpoint, CORS hardening (SOC2)

Body:
  ## Summary
  Production-readiness improvements targeting SOC2 CC7.2, CC7.3, CC6.1.

  ## Changes
  - NEW service/core/logging.py (JSON formatter + X-Request-ID middleware)
  - NEW service/api/v1/endpoints/metrics.py (Prometheus /metrics)
  - MOD service/main.py (RequestIDMiddleware, CORSMiddleware update)
  - MOD service/config.py (ALLOWED_ORIGINS setting)
  - MOD requirements.txt (prometheus_fastapi_instrumentator 7.0.0)
  - MOD docs/design.md (NFR-14, NFR-15, NFR-13 marked implemented)
  - MOD README.md (curl examples, env var docs)

  ## Scope Decision
  Original requirement: "Make the service production-ready"
  Resolved via clarification (SOC2 audit, 2-week window):
    - Structured logging + request IDs (CC7.2)
    - Metrics endpoint (CC7.3)
    - CORS restriction (CC6.1)

  Explicitly deferred:
    - Redis redirect cache
    - CI/CD pipeline
    - Graceful shutdown

  ## Assumptions (confirmed by alice)
  - A1: Request IDs use UUID4 as X-Request-ID header
  - A2: Metrics endpoint unauthenticated (Prometheus standard)
  - A3: CORS origins via ALLOWED_ORIGINS env var

  ## Tests
  8 new (53 total), all passing

  ## Run
  Run ID: orch-ambig-003 | Triggered by: alice | Approved: bob
```

Bob reviews → Approves → Merges.

---

## Stage 7 — RELEASE_READINESS

```
✓ Tests:           53/53 passing
✓ Linter:          clean
✓ Docs:            NFRs updated, README updated
✓ Migration:       none
✓ PR merged:       by bob (TECH_LEAD)
✓ Dependency:      prometheus_fastapi_instrumentator 7.0.0 (no CVEs)
```

**Carol's approval view includes the assumption log and scope decision** — she can see exactly what "production-ready" ended up meaning:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Release Review — orch-ambig-003
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Original: "Make the service production-ready"
Resolved: SOC2-focused observability and access control

  ✓ Structured JSON logging + X-Request-ID (CC7.2)
  ✓ GET /metrics endpoint, unauthenticated (CC7.3)
  ✓ CORS restricted to ALLOWED_ORIGINS env var (CC6.1)

Deferred (documented):
  - Redis redirect cache, CI/CD, graceful shutdown

Assumptions confirmed by alice:
  A1. Request IDs: UUID4 as X-Request-ID header
  A2. Metrics: unauthenticated
  A3. CORS: ALLOWED_ORIGINS env var

Tests: 53/53 | Lint: clean | Docs: updated
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Approve release? [y/n]: y
✓ Approved by carol (RELEASE_MANAGER)
```

---

## User Feedback

```
Run orch-ambig-003 completed in 14m 27s

Total cost:  $0.084  (includes clarification loop LLM calls)
Stages:      9/9 completed, 0 failed, 0 retried
Clarification rounds: 1 (4 questions, all answered)

Rate this output [1-4]: 4
Comment: The clarification questions were targeted and smart.
         The SOC2 mapping was exactly what we needed.
```

---

## Memory Written This Run

| Type | Actor | Content |
|---|---|---|
| fact | system | SOC2 audit is the compliance driver for observability work |
| convention | system | Metrics endpoint must be unauthenticated for Prometheus scraping |
| convention | system | CORS allowed origins controlled via ALLOWED_ORIGINS env var |
| decision | system | Request IDs: UUID4 as X-Request-ID response header |
| preference | bob | request_id must appear in structured log fields, not just response headers |
| convention | bob | ALLOWED_ORIGINS must have a default for local development |

---

## Key Interview Points for This Scenario

**"How does the agent handle ambiguity?"**
> "The agent does not ask 'what do you mean?' — it first reads the codebase and maps gaps against the already-documented NFRs in design.md. Then it asks exactly four targeted questions whose answers determine scope. This is codebase reasoning before requirement clarification, not the reverse."

**"What if the user doesn't know the answer?"**
> "Q2 has an 'I'm not sure' option — the agent picks the highest-value items from its gap analysis. The user retains control but isn't blocked by lack of domain knowledge."

**"Why save deferred items?"**
> "The deferred list is written to the PR body and to orch_memory. A future run of 'add Redis caching' will find the memory entry noting this was a known gap, understand it's part of a planned sequence, and pick up context from the previous run's decisions."

**"What stops the agent from scoping too broadly?"**
> "The 2-week deadline + single compliance standard answer collapses the solution space from 7 items to 3. The agent re-plans after every answer — it doesn't just pass the answers to a static template."

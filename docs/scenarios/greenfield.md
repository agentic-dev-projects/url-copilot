# Scenario 1 — Greenfield
## "Add QR code endpoint GET /api/v1/urls/{id}/qr"

**Scenario type**: Greenfield — new feature, no existing code modified (only new files + router registration).

**What this scenario demonstrates**:
- Agent follows existing codebase conventions before generating code
- Full DAG traversal with all 9 stages
- Format decision (SVG vs PNG) made by agent, not by human
- Human approval gates correctly positioned (before code, at code review, at release)
- Memory captures reviewer preferences for future runs

---

## CLI Command

```bash
python -m orchestrator.run \
  "Add QR code endpoint GET /api/v1/urls/{id}/qr" \
  --token alice_dev_token
```

**Gateway pre-flight**:
```
Auth:         alice_dev_token → alice (DEVELOPER) ✓
Permission:   trigger_run → ALLOWED ✓
Token budget: 0 / 50,000 used today ✓
Rate limit:   0 calls this minute ✓
Injection:    none detected ✓
Input guard:  no PII, no banned ops ✓
trace_id:     tr-001-abc
```

**Planner**:
```
Classifies:  GREENFIELD (new endpoint, no existing code to modify)
Creates run: orch-green-001
Selects DAG: GreenFieldScenario
```

---

## DAG Execution Trace

```
REQUIREMENTS_ANALYSIS         [COMPLETED]
        │
ARCHITECTURE_DESIGN           [COMPLETED → 🔐 Gate #1]
        │
        ├─────────────────────────────────────┐
IMPLEMENTATION_PLAN [COMPLETED]    TEST_PLAN [COMPLETED]   (parallel)
        └──────────────────┬──────────────────┘
                           │ sync point
                    IMPLEMENTATION           [COMPLETED → Gate #2 SKIPPED]
                           │
              ┌────────────┴───────────────┐
         UNIT_TESTS [COMPLETED]   INTEGRATION_TESTS [COMPLETED]  (parallel)
              └────────────┬───────────────┘
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

## Stage 1 — REQUIREMENTS_ANALYSIS

**Model**: gpt-4o-mini (classification task)
**Tools called**: none

Agent receives the requirement and checks for ambiguity.

**Output artifact**:
```json
{
  "feature": "QR code generation for a short URL",
  "type": "greenfield",
  "inputs": ["short_url_id (UUID, path parameter)"],
  "output": "QR code image (format TBD — deferred to architecture stage)",
  "auth_required": true,
  "auth_type": "owner-only",
  "db_change": false,
  "breaking_changes": false,
  "dependency": "QR generation library (TBD)",
  "ambiguities": ["image format: PNG vs SVG"],
  "resolution": "image format decision delegated to architecture stage"
}
```

**Exit gate**: ✅ PASSED — ambiguity identified but resolvable in architecture stage without human input.

---

## Stage 2 — ARCHITECTURE_DESIGN

**Model**: gpt-4o (reasoning task)
**Tools called**:
```
read_file("service/api/v1/endpoints/urls.py")       ← understand existing pattern
read_file("service/api/v1/router.py")               ← how routers are registered
read_file("service/schemas/url.py")                 ← schema conventions
search_codebase("StreamingResponse")                ← how binary responses work
```

**Output artifact**:
```json
{
  "new_files": [
    "service/api/v1/endpoints/qr.py",
    "service/schemas/qr.py"
  ],
  "modified_files": [
    "service/api/v1/router.py",
    "requirements.txt"
  ],
  "endpoint": "GET /api/v1/urls/{id}/qr",
  "auth": "X-API-Key required, owner check enforced",
  "response_type": "image/svg+xml via StreamingResponse",
  "format_rationale": "SVG chosen over PNG: scalable, no PIL/Pillow dependency, smaller file size",
  "library": "segno==1.6.1",
  "errors": {"401": "missing key", "403": "not owner", "404": "url not found"},
  "schema_migration": false
}
```

### 🔐 Gate #1 — Architecture Approval

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[orch-green-001] Architecture approval required
Required role: TECH_LEAD
Triggered by:  alice (cannot approve their own run)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Notify a TECH_LEAD to run:

  python -m orchestrator.run approve \
    --run-id orch-green-001 \
    --gate architecture \
    --token bob_tl_token
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Bob's approval session**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Architecture Review — orch-green-001
Approving as: bob (TECH_LEAD)
Four-eyes:    alice ≠ bob ✓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Proposed design:
  New:      service/api/v1/endpoints/qr.py
            service/schemas/qr.py
  Modified: service/api/v1/router.py
            requirements.txt
  Format:   SVG via StreamingResponse
  Auth:     owner-only
  DB:       no changes

Approve? [y/n]: y
Comment: Good choice on SVG. Use segno library, not qrcode.

✓ Approved by bob (TECH_LEAD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Memory saved:
  [preference] prefer segno over qrcode for QR generation (bob, 2026-08-03)
```

**Audit log entry**:
```json
{"run_id": "orch-green-001", "event_type": "CHECKPOINT_APPROVED",
 "stage_name": "ARCHITECTURE_DESIGN", "actor": "bob", "actor_role": "TECH_LEAD",
 "details": {"gate": "architecture", "comment": "Good choice on SVG. Use segno library, not qrcode."}}
```

---

## Stages 3a + 3b — IMPLEMENTATION_PLAN + TEST_PLAN (Parallel)

Both run concurrently after Gate #1 approval. No human input required.

**Model**: gpt-4o-mini for both

### Implementation Plan artifact:
```json
{
  "tasks": [
    {
      "id": 1,
      "file": "service/api/v1/endpoints/qr.py",
      "action": "create",
      "description": "New endpoint: auth check (owner), generate SVG with segno, return StreamingResponse"
    },
    {
      "id": 2,
      "file": "service/api/v1/router.py",
      "action": "modify",
      "description": "Import and register qr_router with prefix /urls"
    },
    {
      "id": 3,
      "file": "requirements.txt",
      "action": "modify",
      "description": "Add segno==1.6.1"
    }
  ]
}
```

### Test Plan artifact:
```json
{
  "unit_tests": [
    "test_qr_svg_generated: valid short_url_id returns non-empty SVG string",
    "test_qr_invalid_id: unknown id raises 404",
    "test_qr_wrong_owner: non-owner raises 403"
  ],
  "integration_tests": [
    "test_qr_happy_path: register → shorten → GET /qr → 200 + image/svg+xml",
    "test_qr_no_auth: GET /qr without API key → 401",
    "test_qr_not_found: GET /qr for unknown id → 404",
    "test_qr_not_owner: GET /qr for another user's URL → 403",
    "test_qr_content_type: response header is image/svg+xml"
  ],
  "regression": "all 45 existing tests must still pass"
}
```

---

## Stage 4 — IMPLEMENTATION

**Model**: gpt-4o
**Branch created**: `orch/feature/qr-code-orch-green-001`

**Tool calls**:
```
create_branch("orch/feature/qr-code-orch-green-001")     → branch created
read_file("service/api/v1/endpoints/urls.py")             → reads auth pattern
read_file("service/api/v1/router.py")                     → reads registration pattern
write_file("service/api/v1/endpoints/qr.py")              → new endpoint written
write_file("service/api/v1/router.py")                    → router updated
write_file("requirements.txt")                             → segno added
run_linter()                                               → PASSED
```

**Exit gate checks**:
```
Guardrail scan:
  ✓ No hardcoded secrets
  ✓ No dangerous system calls
  ✓ No writes outside service/
Schema change: NOT DETECTED → Gate #2 SKIPPED
Linter: PASSED
```

---

## Stages 5a + 5b — UNIT_TESTS + INTEGRATION_TESTS (Parallel)

**Model**: gpt-4o for both
**Tools called**:
```
write_file("service/tests/unit/test_qr.py")
write_file("service/tests/integration/test_qr_endpoint.py")
run_tests("service/tests/")
```

**Results**:
```
Unit (new):          3/3  PASSED
Integration (new):   5/5  PASSED
Existing suite:     45/45 PASSED
Total:              53/53 PASSED
```

**Exit gate**: ✅ All tests pass including full regression.

---

## Stage 6 — DOCUMENTATION

**Model**: gpt-4o-mini
**Tools called**:
```
read_file("docs/design.md")
write_file("docs/design.md")    ← add QR endpoint to section 4 (API Contracts)
read_file("README.md")
write_file("README.md")         ← add curl example for GET /qr
```

All changes committed to `orch/feature/qr-code-orch-green-001`.

---

## GitHub PR Created

```
PR #5
Title:  feat: add QR code endpoint GET /api/v1/urls/{id}/qr
Branch: orch/feature/qr-code-orch-green-001 → main

Body:
  ## Summary
  - Adds GET /api/v1/urls/{id}/qr returning SVG QR code
  - Uses segno library (lightweight, no PIL dependency)
  - Owner-only access enforced
  - 8 new tests (53 total, all passing)

  ## Changes
  - NEW service/api/v1/endpoints/qr.py
  - MOD service/api/v1/router.py (register qr_router)
  - MOD requirements.txt (add segno==1.6.1)
  - MOD docs/design.md (API contract added)
  - MOD README.md (curl example added)

  ## Orchestration Run
  - Run ID: orch-green-001
  - Triggered by: alice (DEVELOPER)
  - Architecture approved by: bob (TECH_LEAD) at 2026-08-03T10:30:00Z
  - Prompt version: architecture_v1
  - Model: gpt-4o

Required reviewer: bob (TECH_LEAD)
```

**Bob reviews diff on GitHub → Approves → Merges.**

Orchestrator detects merge (polls every 30s) → records `PR_MERGED` in audit log.

---

## Stage 7 — RELEASE_READINESS

**Model**: gpt-4o-mini

**Release checklist artifact**:
```
✓ Tests:       53/53 passing
✓ Linter:      clean
✓ Docs:        API contract + README updated
✓ Migration:   none required
✓ PR merged:   by bob (TECH_LEAD)
✓ Dependency:  segno==1.6.1, no known CVEs
```

### 🔐 Gate #4 — Release Approval

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[orch-green-001] Release approval required
Required role: RELEASE_MANAGER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Carol runs:
  python -m orchestrator.run approve \
    --run-id orch-green-001 --gate release \
    --token carol_rm_token

Release checklist:
  ✓ Tests passing:  53/53
  ✓ Lint:           clean
  ✓ Documentation:  updated
  ✓ Schema changes: none
  ✓ PR merged by:   bob (TECH_LEAD)
  ✓ New dependency: segno 1.6.1 (no CVEs)

Approve release? [y/n]: y

✓ Approved by carol (RELEASE_MANAGER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## User Feedback

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run orch-green-001 completed successfully in 9m 14s

Total cost:  $0.047 (53 LLM calls, 42,100 tokens)
Cache hits:  12 / 53 calls (23%)
Stages:      9/9 completed, 0 failed, 0 retried

Rate this output [1=Poor 2=Acceptable 3=Good 4=Excellent]: 4
Comment (Enter to skip): Clean implementation, tests are thorough.

✓ Feedback recorded. Thank you.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Memory Written This Run

| Type | Actor | Content |
|---|---|---|
| preference | bob | prefer segno over qrcode for QR generation |
| decision | system | QR endpoint format: SVG via StreamingResponse, rationale: scalable, no PIL |

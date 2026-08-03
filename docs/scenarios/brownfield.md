# Scenario 2 — Brownfield
## "Cache frequently accessed short URLs in Redis on the redirect hot path"

**Scenario type**: Brownfield — modifies existing code. Redis already exists in the codebase (used for rate limiting). The redirect endpoint already exists.

**What this scenario demonstrates**:
- Agent reads existing code before proposing anything (codebase reasoning)
- Proactive risk identification: cache invalidation in `url_service.py` — the agent finds this without being told
- Modification of 4 existing files with zero new files
- Regression testing emphasis — all 45 existing tests must still pass
- Gate #2 (schema change) correctly skipped — no DB change needed

---

## CLI Command

```bash
python -m orchestrator.run \
  "Cache frequently accessed short URLs in Redis on the redirect hot path" \
  --token alice_dev_token
```

**Planner**:
```
Classifies:  BROWNFIELD
             (modifies redirect.py which already exists,
              uses redis_client.py which already exists)
Creates run: orch-brown-002
Selects DAG: BrownfieldScenario
```

---

## DAG Execution Trace

```
REQUIREMENTS_ANALYSIS         [COMPLETED — includes impact map]
        │
ARCHITECTURE_DESIGN           [COMPLETED → 🔐 Gate #1]
        │
        ├──────────────────────────────────────────┐
IMPLEMENTATION_PLAN [COMPLETED]        TEST_PLAN [COMPLETED]   (parallel)
        └────────────────────┬─────────────────────┘
                             │ sync point
                      IMPLEMENTATION           [COMPLETED → Gate #2 SKIPPED]
                             │
              ┌──────────────┴─────────────────┐
         UNIT_TESTS [COMPLETED]   INTEGRATION_TESTS [COMPLETED]  (parallel)
              └──────────────┬─────────────────┘
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

**Model**: gpt-4o-mini
**Tools called** (brownfield: agent reads current code FIRST):
```
read_file("service/api/v1/endpoints/redirect.py")
read_file("service/cache/redis_client.py")
read_file("service/services/url_service.py")
read_file("service/config.py")
```

**Output artifact**:
```json
{
  "type": "brownfield",
  "current_state": {
    "redirect.py": "queries DB on every request — no cache check exists",
    "redis_client.py": "exists, used only for rate limiting — no URL caching methods",
    "url_service.py": "has update_url() and delete_url() — both modify URL destination",
    "config.py": "has REDIS_URL but no CACHE_TTL_SECONDS setting"
  },
  "required_changes": [
    "Add cache-aside lookup in redirect.py before DB query",
    "Add get_url / set_url / delete_url methods to redis_client.py",
    "Add cache invalidation calls in url_service.update_url() and url_service.delete_url()",
    "Add CACHE_TTL_SECONDS to config.py"
  ],
  "risk_identified": {
    "name": "Cache invalidation",
    "description": "When a URL is updated or deleted, the stale cached value must be cleared. If url_service.update_url() does not call cache.delete_url(), clients will be redirected to the old destination until TTL expires.",
    "affected_file": "service/services/url_service.py",
    "severity": "HIGH — silent data correctness bug if missed"
  },
  "schema_migration": false,
  "new_files": false
}
```

**Exit gate**: ✅ PASSED. Risk identified and documented — will be addressed in architecture.

---

## Stage 2 — ARCHITECTURE_DESIGN

**Model**: gpt-4o
**Tools called**:
```
read_file("service/api/v1/endpoints/redirect.py")     ← find exact insertion point
read_file("service/cache/redis_client.py")            ← understand existing interface
read_file("service/services/url_service.py")          ← find invalidation insertion points
read_file("service/config.py")                        ← understand settings pattern
```

**Output artifact**:
```json
{
  "pattern": "cache-aside (lazy loading)",
  "cache_key_format": "url_cache:{short_code}",
  "ttl_default_seconds": 3600,
  "new_config_setting": "CACHE_TTL_SECONDS: int = 3600",

  "redirect_flow": [
    "1. GET /{short_code}",
    "2. Redis GET url_cache:{short_code}",
    "3. HIT  → return 302 immediately, DB not queried",
    "4. MISS → SELECT from DB → Redis SET url_cache:{short_code} TTL 3600 → 302"
  ],

  "cache_invalidation": {
    "url_service.update_url": "call redis.delete_url(short_code) BEFORE or AFTER db update",
    "url_service.delete_url": "call redis.delete_url(short_code) AFTER soft-delete"
  },

  "failure_mode": {
    "redis_down": "log warning at WARN level, fall through to DB — MUST NOT raise exception",
    "rationale": "NFR-06 requires graceful degradation: analytics/cache failure must not block redirect"
  },

  "files_modified": [
    "service/cache/redis_client.py   ← add get_url, set_url, delete_url",
    "service/api/v1/endpoints/redirect.py  ← cache check + populate",
    "service/services/url_service.py  ← invalidation on update + delete",
    "service/config.py  ← CACHE_TTL_SECONDS"
  ],
  "new_files": [],
  "schema_migration": false
}
```

### 🔐 Gate #1 — Architecture Approval

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[orch-brown-002] Architecture approval required
Required role: TECH_LEAD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Proposed design:
  Pattern:    Cache-aside, key=url_cache:{code}, TTL=3600s
  Modifies:   redirect.py, redis_client.py, url_service.py, config.py
  New files:  none
  DB change:  none
  Risk:       Cache invalidation in url_service — addressed in design

Approve? [y/n]: y
Comment: Good catch on invalidation. Redis DOWN must never
         block a redirect — make that explicit in the code.

✓ Approved by bob (TECH_LEAD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Memory saved:
  [preference] Redis failures must never block the redirect path (bob, 2026-08-03)
```

---

## Stages 3a + 3b — IMPLEMENTATION_PLAN + TEST_PLAN (Parallel)

### Implementation Plan:
```json
{
  "tasks": [
    {
      "id": 1, "file": "service/cache/redis_client.py",
      "description": "Add get_url(short_code) → str | None — returns original_url or None on miss/error"
    },
    {
      "id": 2, "file": "service/cache/redis_client.py",
      "description": "Add set_url(short_code, original_url, ttl) — SET with EX, silently ignore Redis errors"
    },
    {
      "id": 3, "file": "service/cache/redis_client.py",
      "description": "Add delete_url(short_code) — DEL, silently ignore Redis errors"
    },
    {
      "id": 4, "file": "service/api/v1/endpoints/redirect.py",
      "description": "Add cache check before DB call. On miss: query DB, then SET cache. Wrap in try/except."
    },
    {
      "id": 5, "file": "service/services/url_service.py",
      "description": "In update_url(): call redis.delete_url(short_url.short_code) after DB update"
    },
    {
      "id": 6, "file": "service/services/url_service.py",
      "description": "In delete_url(): call redis.delete_url(short_url.short_code) after soft-delete"
    },
    {
      "id": 7, "file": "service/config.py",
      "description": "Add CACHE_TTL_SECONDS: int = 3600"
    }
  ]
}
```

### Test Plan:
```json
{
  "unit_tests": [
    "test_cache_hit: mock Redis returns URL → redirect returns immediately, DB not called",
    "test_cache_miss: Redis returns None → redirect queries DB, then sets cache",
    "test_cache_invalidation_on_update: update_url called → redis.delete_url called",
    "test_cache_invalidation_on_delete: delete_url called → redis.delete_url called",
    "test_redis_down_redirect_still_works: Redis raises ConnectionError → redirect falls back to DB",
    "test_redis_down_no_exception_raised: Redis error is caught, not re-raised"
  ],
  "integration_tests": [
    "test_redirect_cache_hit: shorten → redirect twice → second call faster (from cache)",
    "test_cache_cleared_on_update: shorten → update URL → redirect → gets new destination",
    "test_cache_cleared_on_delete: shorten → delete → redirect returns 404"
  ],
  "regression": "All 45 existing tests must still pass — no behavior change visible to callers"
}
```

---

## Stage 4 — IMPLEMENTATION

**Model**: gpt-4o
**Branch**: `orch/feature/redis-cache-orch-brown-002`

**Tool calls** (all on existing files — no new files created):
```
create_branch("orch/feature/redis-cache-orch-brown-002")
read_file("service/cache/redis_client.py")
write_file("service/cache/redis_client.py")          ← 3 new methods added
read_file("service/api/v1/endpoints/redirect.py")
write_file("service/api/v1/endpoints/redirect.py")   ← cache-aside + fallback
read_file("service/services/url_service.py")
write_file("service/services/url_service.py")        ← invalidation in update + delete
read_file("service/config.py")
write_file("service/config.py")                      ← CACHE_TTL_SECONDS
run_linter()                                          → PASSED
```

**Exit gate checks**:
```
Guardrail:     no secrets, no dangerous ops ✓
Write safety:  all writes under service/ ✓
Schema change: NOT DETECTED → Gate #2 SKIPPED
Linter:        PASSED ✓
```

---

## Stages 5a + 5b — UNIT_TESTS + INTEGRATION_TESTS (Parallel)

**Tools called**:
```
write_file("service/tests/unit/test_redirect_cache.py")
write_file("service/tests/integration/test_redirect_cache.py")
run_tests("service/tests/")
```

**Results**:
```
New unit tests:        6/6  PASSED
New integration tests: 3/3  PASSED
Existing suite:       45/45 PASSED   ← full regression
Total:                54/54 PASSED
```

**Key test: Redis DOWN does not block redirect**:
```python
def test_redis_down_redirect_still_works(client, db_session, mock_redis_down):
    # Redis configured to raise ConnectionError
    resp = client.get(f"/{short_code}", follow_redirects=False)
    assert resp.status_code == 302          # redirect still works
    assert resp.headers["location"] == original_url
```

**Exit gate**: ✅ All 54 tests pass including full regression.

---

## Stage 6 — DOCUMENTATION

**Tools called**:
```
read_file("docs/design.md")
write_file("docs/design.md")
  ← Update section 5.2 (Redirect Flow) to show cache check step
  ← Add cache key format and TTL to section 5.4 (Key Design Decisions)
```

---

## GitHub PR Created

```
PR #6
Title:  perf: add Redis cache-aside on redirect hot path

Body:
  ## Summary
  - Adds cache-aside lookup to GET /{short_code} before DB query
  - Cache key: url_cache:{short_code}, TTL: 3600s (configurable)
  - Cache invalidation on URL update and delete
  - Redis failures degrade gracefully — redirect always falls back to DB

  ## Risk Addressed
  Cache invalidation: url_service.update_url() and delete_url()
  both call redis.delete_url() — stale redirects not possible.

  ## Changes
  - MOD service/cache/redis_client.py (3 new methods)
  - MOD service/api/v1/endpoints/redirect.py (cache-aside)
  - MOD service/services/url_service.py (invalidation)
  - MOD service/config.py (CACHE_TTL_SECONDS)
  - MOD docs/design.md (updated redirect flow diagram)

  ## Tests
  6 new unit, 3 new integration, 45 regression — 54/54 passing

  ## Run
  Run ID: orch-brown-002 | Triggered by: alice | Approved by: bob
```

Bob reviews: focuses on cache invalidation correctness + Redis fallback path.
Approves → Merges.

---

## Stage 7 — RELEASE_READINESS

```
✓ Tests:       54/54 passing
✓ Linter:      clean
✓ Docs:        redirect flow diagram updated
✓ Migration:   none
✓ PR merged:   by bob (TECH_LEAD)
✓ Config:      new CACHE_TTL_SECONDS — document in .env.example
```

Carol approves release.

---

## User Feedback

```
Run orch-brown-002 completed in 11m 03s

Total cost:  $0.061
Cache hits:  8 / 47 calls (17%)
Stages:      9/9 completed, 0 failed, 0 retried

Rate this output [1-4]: 4
Comment: Impressed it caught the cache invalidation risk without being told.
```

---

## Memory Written This Run

| Type | Actor | Content |
|---|---|---|
| preference | bob | Redis failures must never block the redirect path |
| decision | system | Cache key format: url_cache:{short_code}, TTL from CACHE_TTL_SECONDS config |
| convention | system | Cache invalidation required in url_service on both update and delete |

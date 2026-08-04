# Scenario 1 — Greenfield

A greenfield run takes a natural-language feature request, plans it, implements it on a
feature branch, runs tests, writes documentation, and opens a GitHub PR — with human
approval gates at four checkpoints.

---

## Prerequisites

Before running this scenario make sure the following are running and configured:

| Requirement | Details |
|---|---|
| PostgreSQL | `postgresql://postgres:password@localhost:5432/urlcopilot` |
| Redis | `redis://localhost:6379/0` |
| `.env` file | `OPENAI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO` set |
| Python env | `source .venv/bin/activate` |
| Dependencies | `pip install -r requirements.txt` |

Start backing services with Docker Compose if needed:
```bash
docker-compose up -d db cache
```

---

## Roles and Tokens

| Token | Role | What they can do |
|---|---|---|
| `alice_dev_token` | DEVELOPER | Submit runs |
| `bob_tl_token` | TECH_LEAD | Approve architecture gate |
| `carol_rm_token` | RELEASE_MANAGER | Approve tests, PR, and release gates |

> The four-eyes rule is enforced: the DEVELOPER who submitted the run cannot approve their own gates.

---

## Pipeline Overview

```
DEVELOPER submits requirement
        │
        ▼
requirements_analysis ──► architecture_design
                                    │
                          ⏸ GATE 1: architecture_gate  ← TECH_LEAD approves
                                    │
               ┌────────────────────┴──────────────────┐
     implementation_plan                           test_plan
               └────────────────────┬──────────────────┘
                                    │
                             implementation
                      (creates branch, writes code,
                       runs tests, commits, opens PR)
                                    │
               ┌────────────────────┴──────────────────┐
            unit_tests                       integration_tests
               └────────────────────┬──────────────────┘
                                    │
                             documentation
                                    │
                          ⏸ GATE 2: tests_gate   ← RELEASE_MANAGER approves
                                    │
                          ⏸ GATE 3: pr_gate      ← RELEASE_MANAGER approves
                                    │
                          release_readiness
                                    │
                          ⏸ GATE 4: release_gate ← RELEASE_MANAGER approves
                                    │
                               COMPLETED
```

Total: **9 stages**, **4 gates**, run in a single terminal session across multiple `approve` commands.

---

## Approval Gates

Gates are human checkpoints that pause the pipeline until an authorised role reviews and approves.
No code is written before Gate 1. No PR is merged before Gate 4.

| Gate | Fires after | Approver role | What the approver reviews | Why it exists |
|---|---|---|---|---|
| `architecture_gate` | `architecture_design` | TECH_LEAD | Requirements scope, proposed file changes, new endpoint design, schema change flag | Ensures a senior engineer validates the technical approach **before any code is written** |
| `tests_gate` | `documentation` | RELEASE_MANAGER | Unit test results, integration test results, test gaps, new test files written | Verifies the feature is adequately tested before it goes to code review |
| `pr_gate` | `tests_gate` approval | RELEASE_MANAGER | PR URL, branch name, files written, test results from implementation, documentation changes | Code review checkpoint — confirms the PR was created and the implementation matches the approved design |
| `release_gate` | `release_readiness` | RELEASE_MANAGER | Full release checklist (tests, auth, secrets, docs, error handling, soft-delete, dependencies) | Final sign-off before the run is marked complete — nothing ships without an explicit human approval |

### Four-eyes rule

The user who submits the run (DEVELOPER) cannot approve any gate on their own run.
Every approval must come from a different user with the required role.
This is enforced automatically — the `approve` command rejects the submitter's token.

---

## Step-by-Step

### Step 1 — DEVELOPER submits the requirement

```bash
python -m orchestrator.run run "Add QR code endpoint GET /api/v1/urls/{id}/qr" --token alice_dev_token
```

**What it does:**
- Authenticates the token and verifies the DEVELOPER role has `trigger_run` permission
- Classifies the requirement as `greenfield`
- Creates a run record in the database and assigns a run ID
- Runs `requirements_analysis` then `architecture_design` automatically
- Pauses at `architecture_gate` waiting for a TECH_LEAD to approve

> **Next approver: TECH_LEAD** — use `bob_tl_token`

**Expected output:**
```
Run ID:   orch-<8-char-hex>
Scenario: greenfield

Starting pipeline for run orch-<id>...

Pipeline paused at next gate: architecture_gate
  Required permission: approve_architecture

To continue:
  python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>
```

> **Save the Run ID** — you will pass it to every subsequent `approve` command.

---

### Step 2 — TECH_LEAD approves the architecture gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token bob_tl_token
```

**What it does:**
- Authenticates the token and verifies the TECH_LEAD role has `approve_architecture` permission
- Enforces the four-eyes rule: the approver must not be the same user who submitted the run
- Displays the `requirements_analysis` and `architecture_design` artifacts for review
- Prompts for an optional comment and a yes/no approval decision
- If approved: resumes the pipeline

**Expected output:**
```
Authenticated as: bob
Run ID     : orch-<id>
Gate       : architecture_gate
Submitted by: alice

Requirement: Add QR code endpoint GET /api/v1/urls/{id}/qr

============================================================

  ────────────────────────────────────────────────────────
  REQUIREMENTS ANALYSIS
  ────────────────────────────────────────────────────────

  IN SCOPE:
    • Implementing a new endpoint GET /api/v1/urls/{id}/qr
    • Generating QR codes for URLs given their ID
    ...

  ────────────────────────────────────────────────────────
  ARCHITECTURE DESIGN
  ────────────────────────────────────────────────────────

  NEW ENDPOINTS:
    path: /api/v1/urls/{id}/qr
    method: GET
    auth_required: True
    response_schema: QRResponseSchema
  ...
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by bob. Resuming pipeline...
```

**What runs after approval:**
- `implementation_plan` and `test_plan` run in parallel
- `implementation` runs — creates a feature branch, writes code, runs tests, commits and pushes to the branch, opens a GitHub PR
- `unit_tests` and `integration_tests` run in parallel
- `documentation` runs
- Pipeline pauses at `tests_gate`

```
Pipeline paused at next gate: tests_gate
  Required permission: approve_architecture

To continue:
  python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>
```

> **Next approver: RELEASE_MANAGER** — use `carol_rm_token`

---

### Step 3 — RELEASE_MANAGER approves the tests gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
```

**What it does:**
- Authenticates the token and verifies the RELEASE_MANAGER role has `approve_architecture` permission
- Displays the `unit_tests` and `integration_tests` artifacts — test counts, pass/fail results, gaps identified, and any new test files written
- Prompts for approval

**Expected output:**
```
Authenticated as: carol
Gate       : tests_gate

  ────────────────────────────────────────────────────────
  UNIT TESTS
  ────────────────────────────────────────────────────────

  TEST RESULTS:
    failed: 0
    passed: 46
    success: True

  TEST FILES WRITTEN:
    • service/tests/unit/test_qrcode_generation.py

  ────────────────────────────────────────────────────────
  INTEGRATION TESTS
  ────────────────────────────────────────────────────────

  TEST RESULTS:
    failed: 0
    passed: 3
    success: True

  TEST FILES WRITTEN:
    • service/tests/integration/test_qr_code.py
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by carol. Resuming pipeline...

Pipeline paused at next gate: pr_gate
```

> No stages run between `tests_gate` and `pr_gate` — the pipeline immediately pauses again.

> **Next approver: RELEASE_MANAGER** — use `carol_rm_token`

---

### Step 4 — RELEASE_MANAGER approves the PR gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
```

**What it does:**
- Displays the `implementation` and `documentation` artifacts — the PR URL, branch name, files written, test results from the implementation stage, and documentation changes
- This is the code review checkpoint: the RELEASE_MANAGER verifies the PR was created and the implementation looks correct before proceeding to release readiness

**Expected output:**
```
Authenticated as: carol
Gate       : pr_gate

  ────────────────────────────────────────────────────────
  IMPLEMENTATION
  ────────────────────────────────────────────────────────

  PR URL:
  https://github.com/agentic-dev-projects/url-copilot/pull/<N>

  PR NUMBER:
  <N>

  BRANCH NAME:
  feature/add-qr-endpoint-<run-id>

  TEST RESULTS:
    failed: 0
    passed: 44
    success: True

  FILES WRITTEN:
    • service/api/v1/endpoints/urls.py

  ────────────────────────────────────────────────────────
  DOCUMENTATION
  ────────────────────────────────────────────────────────

  FILES UPDATED:
    • service/api/v1/endpoints/urls.py

  ROUTES DOCUMENTED:
    • GET /api/v1/urls/{url_id}/qr
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by carol. Resuming pipeline...
```

**What runs after approval:**
- `release_readiness` runs — checks tests, auth enforcement, no debug code, documentation, architecture alignment
- Pipeline pauses at `release_gate`

> **Next approver: RELEASE_MANAGER** — use `carol_rm_token`

---

### Step 5 — RELEASE_MANAGER approves the release gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
```

**What it does:**
- Displays the `release_readiness` artifact — a checklist of every quality gate (tests, auth, secrets, soft-delete, dependencies, error handling)
- Final human sign-off before the run is marked complete

**Expected output:**
```
Authenticated as: carol
Gate       : release_gate

  ────────────────────────────────────────────────────────
  RELEASE READINESS
  ────────────────────────────────────────────────────────

  BLOCKERS: (none)

  CHECKLIST:
    tests_pass: True
    auth_enforced: True
    no_debug_code: True
    endpoints_documented: True
    matches_architecture: True
    migration_reversible: N/A
    no_hardcoded_secrets: True
    soft_delete_followed: True
    dependencies_declared: True
    error_handling_present: True

  READY TO SHIP: Yes
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by carol. Resuming pipeline...

============================================================
  RUN SUMMARY — orch-<id>
============================================================
  Requirement : Add QR code endpoint GET /api/v1/urls/{id}/qr
  Scenario    : greenfield
  Cost (USD)  : ~$0.32
  Tokens      : ~117,000
  Stages done : 9
  Stages fail : 0
  Retries     : 0
============================================================

How satisfied are you with this run's output?
  1 = Unusable   2 = Needs work   3 = Good   4 = Excellent
Score [1-4] (or press Enter to skip):
```

> **No further approvals needed** — the run is complete.

---

### Step 6 — (Optional) Check status at any point

```bash
python -m orchestrator.run status --run-id orch-<id> --token <any_token>
```

**Who can run it:** Any authenticated token regardless of role — DEVELOPER, TECH_LEAD, RELEASE_MANAGER, and ADMIN all have access.

**What it does:**
- Shows the current state of the run — which gate it is waiting at, that it is running, or that it has completed
- Lists every stage with its status and attempt number
- Shows a per-stage metrics table: total tokens consumed, cost in USD, average LLM call latency, number of LLM calls, and cache hits

**Expected output (mid-run, waiting at a gate):**
```
============================================================
  RUN STATUS — orch-<id>
============================================================
  Requirement  : Add QR code endpoint GET /api/v1/urls/{id}/qr
  Scenario     : greenfield
  Submitted by : alice
  Created at   : 2025-08-03 14:22:01

  Status       : AWAITING APPROVAL
  Pending gate : tests_gate  (requires: approve_architecture)

  An approver can action this with:
    python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>

  STAGE                          STATUS       ATTEMPT
  ------------------------------ ------------ -------
  requirements_analysis          completed    1
  architecture_design            completed    1
  implementation_plan            completed    1
  test_plan                      completed    1
  implementation                 completed    1
  unit_tests                     completed    1
  integration_tests              completed    1
  documentation                  completed    1

  PER-STAGE METRICS
  STAGE                          TOKENS   COST $   AVG LAT  CALLS  HITS
  ------------------------------ -------- -------- --------- ------ -----
  requirements_analysis           12,450   0.0187    2340ms      2      0
  architecture_design             18,730   0.0281    3120ms      3      1
  implementation                  45,200   0.0678    4890ms      6      2
  ...
============================================================
```

---

### Step 7 — (Optional) Review without approving

```bash
python -m orchestrator.run review --token bob_tl_token
```

**What it does:** Lists all runs currently awaiting approval that are visible to this token's role. Does not prompt for approval — use `approve` when ready to act.

---

## Stage Reference

Each stage is an LLM agent call. The agent reads the codebase, calls tools, and produces a
structured output artifact that is stored in the database and shown at the next gate.

---

### 1. requirements_analysis

**Runs automatically after:** run is submitted  
**Purpose:** Parse the requirement, identify what is in scope, flag ambiguities, and estimate complexity.

The agent reads the requirement text and produces a structured artifact covering: in-scope work,
out-of-scope work, affected files, open questions, whether a schema change is likely, and a
one-sentence scoped requirement statement.

If the requirement is too ambiguous to proceed (e.g. contradictory constraints, missing critical
context), the agent triggers a clarification loop before continuing.

---

### 2. architecture_design

**Runs automatically after:** requirements_analysis  
**Purpose:** Design the technical approach — what files to create or modify, what the new endpoint
looks like, what response schema is needed, and whether a DB migration is required.

The agent uses `read_file` and `search_codebase` to understand existing patterns in the codebase
before proposing anything. The output covers: new endpoints, service changes, new files needed,
schema change flag, risks, and assumptions.

> ⏸ **Pauses here** — TECH_LEAD reviews and approves before any code is written.

---

### 3. implementation_plan _(runs in parallel with test_plan)_

**Runs automatically after:** architecture_gate approval  
**Purpose:** Break the architecture design into ordered implementation tasks — which files to
write, in what order, and what each file should contain.

The output is a task list that the implementation stage follows. Running this in parallel with
`test_plan` saves time since neither depends on the other.

---

### 4. test_plan _(runs in parallel with implementation_plan)_

**Runs automatically after:** architecture_gate approval  
**Purpose:** Define what unit and integration tests need to be written to verify the feature.

The output lists specific test cases by name, the files they go in, and what each test asserts.
This plan is fed into the `unit_tests` and `integration_tests` stages later.

---

### 5. implementation

**Runs automatically after:** implementation_plan and test_plan both complete  
**Purpose:** Write the code, verify it works, and push it to GitHub.

This is the most complex stage. The agent:
1. Calls `create_branch` to create a feature branch from `main` on GitHub
2. Calls `read_file` / `search_codebase` to understand existing code patterns
3. Calls `write_file` to write the implementation to the local filesystem
4. Calls `run_tests` to verify all tests pass locally before committing
5. Calls `commit_and_push` to stage, commit, and push the changes to the remote feature branch
6. Calls `create_pr` to open a pull request on GitHub (feature branch → main)

The output artifact contains: branch name, PR URL, PR number, files written, and test results.

---

### 6. unit_tests _(runs in parallel with integration_tests)_

**Runs automatically after:** implementation  
**Purpose:** Write and run unit tests for the new feature.

The agent reads the test plan, reads the implementation, writes unit test files, and runs the
full test suite. The output reports: test count, pass/fail results, coverage gaps, and which
files were written.

---

### 7. integration_tests _(runs in parallel with unit_tests)_

**Runs automatically after:** implementation  
**Purpose:** Write and run integration tests that exercise the new endpoint end-to-end through
the full HTTP stack (auth → route → service → DB).

Same flow as unit_tests but focused on API-level behaviour. Tests register a user, create data,
call the new endpoint, and assert on the HTTP response.

---

### 8. documentation

**Runs automatically after:** unit_tests and integration_tests both complete  
**Purpose:** Add or update docstrings for all new and modified functions, and update any relevant
route documentation.

The agent reads the implemented files and writes inline documentation. No new files are created —
only existing files are updated.

> ⏸ **Pauses at tests_gate** — RELEASE_MANAGER reviews test results before proceeding.  
> ⏸ **Pauses immediately at pr_gate** — RELEASE_MANAGER reviews the implementation and PR.

---

### 9. release_readiness

**Runs automatically after:** pr_gate approval  
**Purpose:** Perform a final automated quality check against a standardised checklist.

The agent reads the codebase and checks every item on the release checklist:

| Check | What it verifies |
|---|---|
| `tests_pass` | No failing tests in the suite |
| `auth_enforced` | New endpoints require authentication |
| `no_debug_code` | No `print`, `pdb`, or hardcoded debug flags |
| `endpoints_documented` | All new routes have docstrings |
| `matches_architecture` | Implementation matches the approved architecture design |
| `migration_reversible` | Any DB migration can be rolled back (N/A if no migration) |
| `no_hardcoded_secrets` | No API keys or passwords in source files |
| `soft_delete_followed` | Deletions use `is_active = False`, not hard deletes |
| `dependencies_declared` | New libraries added to `requirements.txt` |
| `error_handling_present` | New endpoints return proper 4xx/5xx responses |

The output states whether the feature is **READY TO SHIP** and lists any blockers or warnings.

> ⏸ **Pauses at release_gate** — RELEASE_MANAGER gives final sign-off.  
> After approval the run is marked **COMPLETED**.

---

## What to Verify After the Run

| Check | Where to look |
|---|---|
| Feature branch created | GitHub → branches list → `feature/add-qr-endpoint-<id>` |
| PR opened with implementation | GitHub → Pull Requests → PR title contains the requirement |
| Implementation file committed to branch | PR diff → `service/api/v1/endpoints/urls.py` |
| 44+ tests passed during implementation | PR gate review → `TEST RESULTS: passed: 44` |
| Run summary | Terminal output after release_gate approval |
| All 9 stages completed | `Stages done: 9`, `Stages fail: 0` |

---

## Troubleshooting

**`ERROR: DATABASE_URL is not set`**
— PostgreSQL is not running or `.env` is missing. Run `docker-compose up -d db` and check `.env`.

**`Daily token budget exceeded`**
— The DEVELOPER role's daily token budget is exhausted. Budget resets at midnight UTC.
Increase the limit in `orchestrator/config/rbac.yaml` under `DEVELOPER.daily_token_budget` if needed.

**`WRITE_FILE_REQUIRED: Nothing is staged under service/`**
— The LLM called `commit_and_push` without calling `write_file` first. This can happen if the LLM
reads the codebase and concludes the feature already exists locally. Ensure `service/` is clean
(run `git checkout HEAD -- service/` before starting) and retry.

**`fatal: pathspec 'service/' did not match any files`**
— Internal path resolution error. Verify `orchestrator/tools/github_client.py` uses
`os.path.join(os.path.dirname(__file__), "..", "..")` (two levels up, not three) for `repo_root`.

**PR creation fails with GitHub 422**
— The feature branch has no commits (commit_and_push failed before this). Check
`orchestrator_app.log` for the root cause, fix it, and run again.

# Scenario 2 — Brownfield

A brownfield run takes a natural-language request to modify or extend an **existing** feature,
plans it, implements it on a feature branch, runs tests, writes documentation, and opens a
GitHub PR — with human approval gates at four checkpoints.

The key difference from greenfield: the agent reads existing code **before** proposing anything.
It identifies what files need to change, what must stay backwards-compatible, and what risks
the modification introduces — then the TECH_LEAD reviews this before any code is written.

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
| `bob_tl_token` | TECH_LEAD | Approve architecture and tests gates |
| `carol_rm_token` | RELEASE_MANAGER | Approve PR and release gates |

> The four-eyes rule is enforced: the DEVELOPER who submitted the run cannot approve their own gates.

---

## Pipeline Overview

```
DEVELOPER submits requirement
        │
        ▼
requirements_analysis ──► architecture_design
                    (reads existing code first)
                                    │
                          ⏸ GATE 1: architecture_gate  ← TECH_LEAD approves
                                    │
               ┌────────────────────┴──────────────────┐
     implementation_plan                           test_plan
               └────────────────────┬──────────────────┘
                                    │
                             implementation
                      (reads existing files, modifies them,
                       runs tests, commits, opens PR)
                                    │
               ┌────────────────────┴──────────────────┐
            unit_tests                       integration_tests
               └────────────────────┬──────────────────┘
                                    │
                             documentation
                                    │
                          ⏸ GATE 2: tests_gate   ← TECH_LEAD approves
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
| `architecture_gate` | `architecture_design` | TECH_LEAD | Affected files, proposed changes, backwards-compatibility risks, schema change flag | Ensures a senior engineer validates the modification approach **before any code is written** |
| `tests_gate` | `documentation` | TECH_LEAD | Unit test results, integration test results, regression coverage, new test files written | Verifies the change is adequately tested and existing tests still pass |
| `pr_gate` | `tests_gate` approval | RELEASE_MANAGER | PR URL, branch name, files modified, test results from implementation, documentation changes | Code review checkpoint — confirms the PR was created and the implementation matches the approved design |
| `release_gate` | `release_readiness` | RELEASE_MANAGER | Full release checklist (tests, auth, secrets, docs, error handling, soft-delete, dependencies) | Final sign-off before the run is marked complete — nothing ships without an explicit human approval |

### Four-eyes rule

The user who submits the run (DEVELOPER) cannot approve any gate on their own run.
Every approval must come from a different user with the required role.
This is enforced automatically — the `approve` command rejects the submitter's token.

---

## Step-by-Step

### Step 1 — DEVELOPER submits the requirement

```bash
python -m orchestrator.run run "Add pagination to the GET /api/v1/urls endpoint using skip and limit query parameters" --token alice_dev_token
```

**What it does:**
- Authenticates the token and verifies the DEVELOPER role has `trigger_run` permission
- Classifies the requirement as `brownfield` (modifies an existing endpoint)
- Creates a run record in the database and assigns a run ID
- Runs `requirements_analysis` then `architecture_design` automatically — the agent reads existing files before proposing changes
- Pauses at `architecture_gate` waiting for a TECH_LEAD to approve

> **Next approver: TECH_LEAD** — use `bob_tl_token`

**Expected output:**
```
Run ID:   orch-<8-char-hex>
Scenario: brownfield

Starting pipeline for run orch-<id>...

Pipeline paused — waiting for gate approval.

  Run ID : orch-<id>
  Gate   : architecture_gate  (requires: approve_architecture)

Next steps:
  # Approver — see all pending reviews:
  python -m orchestrator.run review --token <approver_token>

  # Approver — approve this specific run:
  python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>

  # Check current status and per-stage metrics:
  python -m orchestrator.run status --run-id orch-<id> --token <your_token>
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
- Key things to check: which existing files are affected, what risks were identified, whether a schema migration is needed
- Prompts for an optional comment and a yes/no approval decision
- If approved: resumes the pipeline

**Expected output:**
```
Authenticated as: bob
Run ID     : orch-<id>
Gate       : architecture_gate
Submitted by: alice

Requirement: Add pagination to the GET /api/v1/urls endpoint using skip and limit query parameters

============================================================

  ────────────────────────────────────────────────────────
  REQUIREMENTS ANALYSIS
  ────────────────────────────────────────────────────────

  IN SCOPE:
    • Modify the GET /api/v1/urls endpoint to accept 'skip' and 'limit' query parameters.
    • Implement logic to handle pagination in the URL retrieval service function.
    • Update Pydantic schemas if necessary to reflect changes in the API response format.
    • Ensure new functionality is covered by both unit and integration tests.

  OUT OF SCOPE:
    • Changes to how URLs are stored or represented beyond pagination.
    • Modifications to endpoints other than GET /api/v1/urls.

  AFFECTED FILES:
    • service/api/v1/endpoints/urls.py
    • service/services/url_service.py
    • service/schemas/url.py

  SCHEMA CHANGE LIKELY: YES ⚠

  ────────────────────────────────────────────────────────
  ARCHITECTURE DESIGN
  ────────────────────────────────────────────────────────

  RISKS:
    • Potential for breaking changes if the existing consumer expects the full list of URLs.
    • Improper handling of 'skip' and 'limit' values may lead to inefficient queries.

  ASSUMPTIONS:
    • Default values for 'skip' and 'limit' are set to 0 and 10 respectively.
    • A maximum limit of 100 is enforced to prevent overly large responses.

  SERVICE CHANGES:
    • service/api/v1/endpoints/urls.py: Modify the GET endpoint to process 'skip' and 'limit'.
    • service/services/url_service.py: Update retrieval logic to handle pagination.
    • service/schemas/url.py: Add pagination response schema if needed.

  SCHEMA CHANGE REQUIRED: No
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by bob. Resuming pipeline...
```

**What runs after approval:**
- `implementation_plan` and `test_plan` run in parallel
- `implementation` runs — reads existing files, modifies them, runs tests, commits and pushes to a feature branch, opens a GitHub PR
- `unit_tests` and `integration_tests` run in parallel
- `documentation` runs
- Pipeline pauses at `tests_gate`

```
Pipeline paused at next gate: tests_gate
  Required permission: approve_architecture

To continue:
  python -m orchestrator.run review --token <approver_token>
  python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>

To see the current status:
  python -m orchestrator.run status --run-id orch-<id> --token <your_token>
```

> **Next approver: TECH_LEAD** — use `bob_tl_token`

---

### Step 3 — TECH_LEAD approves the tests gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token bob_tl_token
```

**What it does:**
- Displays the `unit_tests` and `integration_tests` artifacts — test counts, pass/fail results, gaps identified, and any new test files written
- Key things to check: regression tests still pass, new tests cover the changed behaviour
- Prompts for approval

**Expected output:**
```
Authenticated as: bob
Gate       : tests_gate

  ────────────────────────────────────────────────────────
  UNIT TESTS
  ────────────────────────────────────────────────────────

  TEST RESULTS:
    failed: 0
    passed: 44
    success: True

  TEST FILES WRITTEN:
    • service/tests/unit/test_urls_pagination.py

  ────────────────────────────────────────────────────────
  INTEGRATION TESTS
  ────────────────────────────────────────────────────────

  TEST RESULTS:
    failed: 0
    passed: 1
    success: True

  TEST FILES WRITTEN:
    • service/tests/integration/test_urls_pagination.py
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by bob. Resuming pipeline...

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
- Displays the `implementation` and `documentation` artifacts — the PR URL, branch name, files modified, test results from the implementation stage, and documentation changes
- This is the code review checkpoint: the RELEASE_MANAGER verifies the PR was created and that only existing files were modified (no unplanned new files)

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
  feature/add-pagination-<run-id>

  TEST RESULTS:
    failed: 2
    passed: 44
    success: False

  FILES WRITTEN:
    • service/api/v1/endpoints/urls.py
    • service/services/url_service.py

  DEVIATIONS FROM ARCHITECTURE: (none)

  ────────────────────────────────────────────────────────
  DOCUMENTATION
  ────────────────────────────────────────────────────────

  FILES UPDATED:
    • service/api/v1/endpoints/urls.py
    • service/services/url_service.py

  ROUTES DOCUMENTED:
    • /api/v1/urls
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
- Displays the `release_readiness` artifact — a checklist of every quality gate
- The RELEASE_MANAGER decides whether any blockers prevent shipping — if `tests_pass: False`, they can still approve and note the exceptions
- Final human sign-off before the run is marked complete

**Expected output:**
```
Authenticated as: carol
Gate       : release_gate

  ────────────────────────────────────────────────────────
  RELEASE READINESS
  ────────────────────────────────────────────────────────

  BLOCKERS:
    • 2 failing tests in the suite — review before merging PR.

  CHECKLIST:
    tests_pass: False
    auth_enforced: True
    no_debug_code: True
    endpoints_documented: True
    matches_architecture: True
    migration_reversible: N/A
    no_hardcoded_secrets: True
    soft_delete_followed: True
    dependencies_declared: True
    error_handling_present: True

  READY TO SHIP: No
============================================================

Review comment (optional, press Enter to skip): lgtm
Approve? [y/n]: y

Approved by carol. Resuming pipeline...

============================================================
  RUN SUMMARY — orch-<id>
============================================================
  Requirement : Add pagination to the GET /api/v1/urls endpoint using skip and limit que
  Scenario    : brownfield
  Cost (USD)  : $0.50
  Tokens      : ~172,000
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
  Requirement  : Add pagination to the GET /api/v1/urls endpoint...
  Scenario     : brownfield
  Submitted by : alice
  Created at   : 2026-08-04 20:24:32

  Status       : AWAITING APPROVAL
  Pending gate : pr_gate  (requires: approve_release)

  An approver can action this with:
    python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>

  STAGE                          STATUS       ATTEMPT
  ------------------------------ ------------ -------
  requirements_analysis          completed    1
  architecture_design            completed    1
  implementation_plan            completed    1
  test_plan                      completed    1
  implementation                 completed    1
  integration_tests              completed    1
  unit_tests                     completed    1
  documentation                  completed    1

  PER-STAGE METRICS
  STAGE                            TOKENS   COST $   AVG LAT  CALLS  HITS
  ------------------------------ -------- -------- --------- ------ -----
  classifier                          307   0.0001    1426ms      1     0
  requirements_analysis             2,254   0.0084    6550ms      1     0
  architecture_design               3,193   0.0112    8105ms      1     0
  implementation_plan               7,932   0.0242    3105ms      2     0
  test_plan                         6,310   0.0212    4692ms      2     0
  implementation                   43,868   0.1208    9085ms      5     0
  integration_tests                11,608   0.0338    7506ms      3     0
  unit_tests                       15,036   0.0423   11993ms      4     0
  documentation                     4,415   0.0128    1830ms      2     0
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

In brownfield runs the agent is instructed to read existing files **before** proposing or
writing anything. This ensures the implementation follows existing patterns and does not
accidentally break backwards compatibility.

---

### 1. requirements_analysis

**Runs automatically after:** run is submitted  
**Purpose:** Parse the requirement, identify what existing code is in scope, flag risks, and estimate complexity.

The agent reads the requirement text and produces a structured artifact covering: in-scope work,
out-of-scope work, affected existing files, open questions, whether a schema change is likely, and a
one-sentence scoped requirement statement.

In brownfield context, the agent also calls `read_file` on the affected files to understand current
state before describing what needs to change.

---

### 2. architecture_design

**Runs automatically after:** requirements_analysis  
**Purpose:** Design the modification approach — which existing files to change, what the changes
look like, and whether a DB migration is required.

The agent uses `read_file` and `search_codebase` to understand existing patterns before proposing
anything. The output covers: service changes, modified files, schema change flag, risks, and
backwards-compatibility assumptions. No new files are created unless strictly necessary.

> ⏸ **Pauses here** — TECH_LEAD reviews and approves before any code is written.

---

### 3. implementation_plan _(runs in parallel with test_plan)_

**Runs automatically after:** architecture_gate approval  
**Purpose:** Break the architecture design into ordered implementation tasks — which files to
modify, in what order, and what each change should contain.

---

### 4. test_plan _(runs in parallel with implementation_plan)_

**Runs automatically after:** architecture_gate approval  
**Purpose:** Define what unit and integration tests need to be written or updated to verify the change.

In brownfield context, the test plan explicitly includes a regression requirement: all existing
tests must still pass after the modification.

---

### 5. implementation

**Runs automatically after:** implementation_plan and test_plan both complete  
**Purpose:** Modify the existing code, verify it works, and push it to GitHub.

The agent:
1. Calls `create_branch` to create a feature branch from `main` on GitHub
2. Calls `read_file` on each file to be modified — reads BEFORE writing
3. Calls `write_file` to write the modified files (overwrites the existing file with changes applied)
4. Calls `run_tests` to verify all tests pass locally before committing
5. Calls `commit_and_push` to stage, commit, and push the changes to the remote feature branch
6. Calls `create_pr` to open a pull request on GitHub (feature branch → main)

The output artifact contains: branch name, PR URL, PR number, files modified, and test results.

---

### 6. unit_tests _(runs in parallel with integration_tests)_

**Runs automatically after:** implementation  
**Purpose:** Write and run unit tests for the modified behaviour.

The agent reads the test plan, reads the modified implementation, writes unit test files, and runs
the full test suite. The output reports: test count, pass/fail results, coverage gaps, and which
files were written.

---

### 7. integration_tests _(runs in parallel with unit_tests)_

**Runs automatically after:** implementation  
**Purpose:** Write and run integration tests that exercise the modified endpoint end-to-end through
the full HTTP stack (auth → route → service → DB).

---

### 8. documentation

**Runs automatically after:** unit_tests and integration_tests both complete  
**Purpose:** Add or update docstrings for all modified functions, and update any relevant
route documentation.

The agent reads the modified files and updates inline documentation. No new files are created —
only existing files are updated.

> ⏸ **Pauses at tests_gate** — TECH_LEAD reviews test results before proceeding.  
> ⏸ **Pauses immediately at pr_gate** — RELEASE_MANAGER reviews the implementation and PR.

---

### 9. release_readiness

**Runs automatically after:** pr_gate approval  
**Purpose:** Perform a final automated quality check against a standardised checklist.

The agent reads the codebase and checks every item on the release checklist:

| Check | What it verifies |
|---|---|
| `tests_pass` | No failing tests in the suite |
| `auth_enforced` | Modified endpoints still require authentication |
| `no_debug_code` | No `print`, `pdb`, or hardcoded debug flags |
| `endpoints_documented` | All modified routes have docstrings |
| `matches_architecture` | Implementation matches the approved architecture design |
| `migration_reversible` | Any DB migration can be rolled back (N/A if no migration) |
| `no_hardcoded_secrets` | No API keys or passwords in source files |
| `soft_delete_followed` | Deletions use `is_active = False`, not hard deletes |
| `dependencies_declared` | New libraries added to `requirements.txt` |
| `error_handling_present` | Modified endpoints return proper 4xx/5xx responses |

The output states whether the feature is **READY TO SHIP** and lists any blockers or warnings.
The RELEASE_MANAGER can approve even with blockers if they judge them acceptable for the release.

> ⏸ **Pauses at release_gate** — RELEASE_MANAGER gives final sign-off.  
> After approval the run is marked **COMPLETED**.

---

## What to Verify After the Run

| Check | Where to look |
|---|---|
| Feature branch created | GitHub → branches list → `feature/add-pagination-<id>` |
| PR opened with modifications | GitHub → Pull Requests → PR title contains the requirement |
| Only existing files modified | PR diff → no unexpected new files outside `service/tests/` |
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
— The LLM called `commit_and_push` without calling `write_file` first. Ensure `service/` is clean
(run `git checkout HEAD -- service/` before starting) and retry.

**`tests_pass: False` at release_gate**
— The LLM may have written a test file with a syntax error, causing collection to fail. Check
`service/tests/` for any new files and run `python -m pytest service/tests/ -v` locally to identify
the broken file. Delete the broken test file and re-run or approve the gate anyway if the core
implementation tests pass.

**PR creation fails with GitHub 422**
— The feature branch has no commits (commit_and_push failed before this). Check
`orchestrator_app.log` for the root cause, fix it, and run again.

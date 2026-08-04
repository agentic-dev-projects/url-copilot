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

## Users and Tokens

| Token | User | Role | What they can do |
|---|---|---|---|
| `alice_dev_token` | alice | DEVELOPER | Submit runs |
| `bob_tl_token` | bob | TECH_LEAD | Approve architecture gate |
| `carol_rm_token` | carol | RELEASE_MANAGER | Approve tests, PR, and release gates |

> The four-eyes rule is enforced: alice cannot approve her own run.

---

## Pipeline Overview

```
Alice submits requirement
        │
        ▼
requirements_analysis ──► architecture_design
                                    │
                          ⏸ GATE 1: architecture_gate  ← Bob approves
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
                          ⏸ GATE 2: tests_gate  ← Carol approves
                                    │
                          ⏸ GATE 3: pr_gate     ← Carol approves
                                    │
                          release_readiness
                                    │
                          ⏸ GATE 4: release_gate ← Carol approves
                                    │
                               COMPLETED
```

Total: **9 stages**, **4 gates**, run in a single terminal session across multiple `approve` commands.

---

## Step-by-Step

### Step 1 — Alice submits the requirement

```bash
python -m orchestrator.run run "Add QR code endpoint GET /api/v1/urls/{id}/qr" --token alice_dev_token
```

**What it does:**
- Authenticates alice as DEVELOPER
- Classifies the requirement as `greenfield`
- Creates a run record in the database and assigns a run ID
- Runs `requirements_analysis` then `architecture_design` automatically
- Pauses at `architecture_gate` waiting for a TECH_LEAD to approve

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

### Step 2 — Bob reviews and approves the architecture gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token bob_tl_token
```

**What it does:**
- Authenticates bob as TECH_LEAD
- Displays the `requirements_analysis` and `architecture_design` artifacts from the run
- Prompts bob for an optional comment and a yes/no approval decision
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

---

### Step 3 — Carol approves the tests gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
```

**What it does:**
- Authenticates carol as RELEASE_MANAGER
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

---

### Step 4 — Carol approves the PR gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
```

**What it does:**
- Displays the `implementation` and `documentation` artifacts — the PR URL, branch name, files written, test results from the implementation stage, and documentation changes
- This is the code review checkpoint: carol verifies the PR was created and the implementation looks correct before proceeding to release readiness

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

---

### Step 5 — Carol approves the release gate

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

---

### Step 6 — (Optional) Check status at any point

```bash
python -m orchestrator.run status --run-id orch-<id> --token alice_dev_token
```

**What it does:** Shows the current state of a run — which gate it is waiting at, or that it has completed.

---

### Step 7 — (Optional) Review without approving

```bash
python -m orchestrator.run review --token bob_tl_token
```

**What it does:** Lists all runs currently awaiting approval that are visible to this token's role. Does not prompt for approval — use `approve` when ready to act.

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

**`Daily token budget exceeded for 'alice'`**
— Alice's daily token budget (DEVELOPER role) is exhausted. Budget resets at midnight UTC.
Increase the limit in `orchestrator/config/rbac.yaml` under `DEVELOPER.daily_token_budget` if needed.

**`WRITE_FILE_REQUIRED: Nothing is staged under service/`**
— The LLM called `commit_and_push` without calling `write_file` first. This can happen if the LLM
reads the codebase and concludes the feature already exists locally. Ensure `service/` is clean
(run `git checkout HEAD -- service/` before starting) and retry.

**`fatal: pathspec 'service/' did not match any files`**
— Internal path resolution error. Verify `orchestrator/tools/github_client.py` uses
`os.path.join(os.path.dirname(__file__), "..", "..")` (two levels up, not three) for `repo_root`.

**PR creation fails with GitHub 422**
— The feature branch has no commits (commit_and_push failed silently before this). Check the
`orchestrator_app.log` for the root cause, fix it, and run again.

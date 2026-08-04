# Quick Start

Everything you need to run any orchestrator scenario — prerequisites, roles, services, and all CLI commands in one place.

**Scenario walkthroughs:**
- [Scenario 1 — Greenfield](scenarios/greenfield.md) — add a brand-new feature from scratch
- [Scenario 2 — Brownfield](scenarios/brownfield.md) — modify or extend an existing feature
- [Scenario 3 — Ambiguous](scenarios/ambiguous.md) — resolve a vague requirement via Q&A, then implement

**Reference:**
- [Gates Reference](GATES.md) — what each gate reviews, who approves it, RBAC table, four-eyes rule

---

## Prerequisites

| Requirement | Details |
|---|---|
| Python 3.11+ | `python --version` |
| PostgreSQL | Running at `postgresql://postgres:password@localhost:5432/urlcopilot` |
| Redis | Running at `redis://localhost:6379/0` |
| `.env` file | `OPENAI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO` set |
| Python env | `.venv` activated — `source .venv/bin/activate` |
| Dependencies | `pip install -r requirements.txt` |

Start backing services:
```bash
docker-compose up -d db cache
```

Run database migrations (first time only):
```bash
alembic upgrade head
```

---

## Roles and Tokens

| Token | Role | What they can do |
|---|---|---|
| `alice_dev_token` | DEVELOPER | Submit runs, answer clarification questions, check status |
| `bob_tl_token` | TECH_LEAD | Everything DEVELOPER can + approve `architecture_gate` and `tests_gate` |
| `carol_rm_token` | RELEASE_MANAGER | Everything TECH_LEAD can + approve `pr_gate` and `release_gate` |

> The four-eyes rule means the DEVELOPER who submits a run cannot approve their own gates.  
> See [Gates Reference — RBAC](GATES.md#rbac--who-can-approve-what) for the full permission matrix.

---

## CLI Commands

### Submit a new run

```bash
python -m orchestrator.run run "<requirement>" --token alice_dev_token
```

The orchestrator classifies the requirement as `greenfield`, `brownfield`, or `ambiguous` and starts the pipeline. **Save the Run ID** printed in the output — you pass it to every subsequent command.

### Approve (or reject) a paused gate

```bash
python -m orchestrator.run approve --run-id orch-<id> --token <approver_token>
```

Displays artifacts for the current gate, prompts for an optional review comment, and asks `Approve? [y/n]`.

### Check run status and per-stage metrics

```bash
python -m orchestrator.run status --run-id orch-<id> --token <any_token>
```

Any authenticated token can call this. Shows: current gate, stage completion table, and per-stage token/cost/latency breakdown.

### List pending approvals

```bash
python -m orchestrator.run review --token <approver_token>
```

Lists all runs currently awaiting approval visible to this token's role.

---

## Typical session (greenfield example)

```bash
# 1. Alice submits
python -m orchestrator.run run "Add QR code endpoint GET /api/v1/urls/{id}/qr" --token alice_dev_token
# → prints orch-<id>, pauses at architecture_gate

# 2. Bob approves architecture
python -m orchestrator.run approve --run-id orch-<id> --token bob_tl_token
# → pipeline runs implementation + tests, pauses at tests_gate

# 3. Bob approves tests
python -m orchestrator.run approve --run-id orch-<id> --token bob_tl_token
# → pauses immediately at pr_gate

# 4. Carol approves PR
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
# → release_readiness runs, pauses at release_gate

# 5. Carol approves release
python -m orchestrator.run approve --run-id orch-<id> --token carol_rm_token
# → run completes, prints summary

# At any point — check status (any token)
python -m orchestrator.run status --run-id orch-<id> --token alice_dev_token
```

---

## Clean up between runs

LLM-written files stay on local `main` after each run so subsequent stages can read them. Before starting the next run, clean them up:

```bash
git checkout HEAD -- service/
git clean -fd service/
```

The orchestrator also does this automatically at the start of each new run.

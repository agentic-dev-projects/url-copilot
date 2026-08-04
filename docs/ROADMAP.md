# Roadmap — Future Improvements

This document lists planned and potential improvements to url-copilot, grouped by theme. Items are roughly ordered from near-term (low effort, high value) to longer-term (higher effort or dependency on earlier work).

---

## UI — CLI to React Dashboard

The CLI is the fastest way to validate the orchestrator, but a React UI would make the system far more usable for teams.

| Feature | Description |
|---|---|
| **Run submission form** | Form to submit a new requirement with role-based access (DEVELOPER only) |
| **Live pipeline DAG** | Visual representation of the 9-stage graph — stages light up green/red in real time via WebSocket or SSE |
| **Approvals inbox** | TECH_LEAD and RELEASE_MANAGER see pending gates as a card list with artifact viewer inline |
| **Approve/reject modal** | Review artifacts, add a comment, and click Approve or Reject — no terminal needed |
| **Run history** | Table of all runs with status, cost, token count, and links to feature branch/PR |
| **Per-stage metrics panel** | The same table currently shown by `status` — rendered as a chart |

**Migration path:**
1. Wrap `handle_run`, `handle_approve`, `handle_status` as FastAPI endpoints (add to `service/api/v1/`)
2. Add WebSocket or SSE endpoint for live stage progress events
3. Build React frontend consuming those endpoints
4. All RBAC and four-eyes logic stays in the existing orchestrator layer

---

## Automation — Reduce Manual Steps

| Feature | Description |
|---|---|
| **Auto PR merge on release_gate** | When `release_gate` is approved and `tests_pass: True`, automatically merge the feature branch into main via GitHub API |
| **Slack / email notifications** | Notify the next approver when a gate fires — "Run orch-abc123 is waiting at tests_gate for your review" |
| **Auto-close stale runs** | Runs waiting at a gate for more than N days are automatically rejected and the submitter is notified |
| **Branch cleanup** | After a PR is merged or a run is rejected, delete the feature branch from GitHub automatically |
| **Re-run failed stages** | Allow a TECH_LEAD to re-trigger a single failed stage without restarting the whole run |

---

## Pipeline — Smarter Execution

| Feature | Description |
|---|---|
| **Self-healing test fixes** | When the test stage detects a syntax error or collection failure in LLM-written code, automatically trigger a fix loop before reporting to the gate |
| **Schema migration dry-run at architecture_gate** | Run `alembic upgrade head --sql` (dry run) as part of architecture review so the reviewer sees the exact SQL before approving |
| **Parallel brownfield runs** | Allow multiple brownfield runs on different files to execute concurrently without conflicting |
| **Incremental implementation** | For large features, split implementation into multiple commits — each verified independently before the next starts |
| **Cost budget enforcement** | Set a per-run cost ceiling; if the LLM exceeds it, pause and ask the DEVELOPER whether to continue |
| **Retry budget per stage** | Currently stages retry on transient errors. Add a configurable max-retry limit so a stuck stage fails fast instead of looping |

---

## Observability — Understand What's Happening

| Feature | Description |
|---|---|
| **LangSmith trace links** | Each run's status output includes a direct link to the LangSmith trace for that run — click to see every LLM call with tokens, latency, and prompt |
| **Cost dashboard** | Aggregate view of spend by run, by user, by stage — helps identify which stage is most expensive |
| **Alert on budget spike** | If a single run exceeds a threshold (e.g. $1.00), send a notification to ADMIN |
| **Structured log export** | Ship `orchestrator_app.log` to a log aggregator (Datadog, CloudWatch) for centralized search and alerting |
| **Run comparison** | Side-by-side diff of two runs on the same requirement — useful for evaluating prompt changes |

---

## Security & Compliance

| Feature | Description |
|---|---|
| **Real GitHub user auth** | Replace static demo tokens (`alice_dev_token`) with OAuth-based GitHub login so tokens map to real GitHub identities |
| **Audit log export** | Export the `orch_audit` table as a signed, immutable log for compliance reviews |
| **Secret scanning at release_gate** | Run `truffleHog` or `detect-secrets` on the feature branch diff before the release_gate artifact is generated |
| **IP allowlisting for gate approvals** | Restrict gate approvals to known corporate IP ranges — relevant for SOX/SOC2 change-control requirements |
| **Time-locked releases** | Block `release_gate` approvals outside of approved deployment windows (e.g. no releases on Fridays) |

---

## Multi-Model & AI Improvements

| Feature | Description |
|---|---|
| **Model routing** | Use a cheaper model (GPT-4o-mini) for classification, test writing, and documentation; reserve GPT-4o for architecture design and implementation |
| **Claude for implementation** | Evaluate Anthropic Claude (claude-opus-4, claude-sonnet-4) for the implementation stage — longer context window benefits large file rewrites |
| **Prompt versioning UI** | View and A/B test different prompt versions from the dashboard without editing files manually |
| **Evaluator feedback loop** | The existing `evaluator/` layer scores each stage output. Feed low scores back into a retry with a refined prompt instead of just logging them |
| **Memory recall at architecture_gate** | Surface relevant prior decisions from `orch_memory` during architecture review — "last time we added an endpoint we decided X" |

---

## Developer Experience

| Feature | Description |
|---|---|
| **`orchestrator doctor` command** | Pre-flight check: verifies DATABASE_URL, OPENAI_API_KEY, GITHUB_TOKEN, Redis connection, and alembic head — prints pass/fail for each |
| **Dry-run mode** | Run the full pipeline without writing files or creating a PR — useful for previewing what the LLM would do |
| **`orchestrator replay`** | Re-run a completed run from a specific stage — useful after fixing a prompt |
| **VS Code extension** | Submit runs and approve gates directly from the editor sidebar |
| **Multi-repo support** | Target a different repository per run — useful for teams with multiple services |

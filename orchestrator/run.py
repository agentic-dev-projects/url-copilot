"""
orchestrator.run — CLI entry point for the AI SDLC Orchestrator.

Commands
--------
run      Trigger a new orchestration run for a natural-language requirement.
approve  Approve a human gate on a paused run (used by TECH_LEAD / RELEASE_MANAGER).

Usage
-----
    # Start a run (as a developer)
    python -m orchestrator.run "Add QR code endpoint GET /api/v1/urls/{id}/qr" \\
        --token alice_dev_token

    # Approve an architecture gate (as tech lead, in a second terminal)
    python -m orchestrator.run approve \\
        --run-id orch-green-001 \\
        --gate architecture \\
        --token bob_tl_token

Notes
-----
- Tokens are resolved via orchestrator/config/users.yaml (mock auth for prototype).
- In production, replace the YAML lookup with an OAuth/SSO token introspection call
  inside TokenAuthenticator — no other code changes required.
- All LLM calls flow through AIGateway, which enforces auth, RBAC, rate limits,
  input validation, output guardrails, and cost tracking before touching OpenAI.
"""

# Implementation added in Phase 17.
# Stub exists here so `python -m orchestrator.run` gives a clear not-yet-implemented
# message rather than a ModuleNotFoundError.

import sys


def main() -> None:
    """Dispatch CLI subcommands: run or approve."""
    print(
        "Orchestrator CLI — not yet implemented.\n"
        "Implementation begins at Phase 17 of docs/IMPLEMENTATION_PLAN.md"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()

"""
orchestrator.governance — RBAC checkpoints and SDLC audit logging.

Files
-----
checkpoint.py   RBACCheckpoint — validates that the approving user has the
                required role and is not the same person who triggered the run
                (four-eyes principle).  Prompts [y/n] at the CLI and records
                the decision to orch_audit_events.

audit.py        AuditLogger — append-only event log to orch_audit_events.
                This table is NEVER updated — only INSERTed into.  Every
                significant state change in a run produces an audit event:
                stage transitions, human approvals, AI evaluations, memory
                writes, PR lifecycle events.

Four-eyes principle
-------------------
The user who triggers a run (--token alice_dev_token) cannot be the same user
who approves its architecture gate.  This is checked in RBACCheckpoint by
comparing CurrentUser.github_login against RunContext.triggered_by.  It
mirrors the SOX change-control requirement common in financial services:
the developer who writes the code cannot be the sole approver of that change.

Audit log immutability
-----------------------
orch_audit_events has no UPDATE or DELETE path in the application.  The only
write operation is INSERT.  In production this table would also be protected
by a database-level trigger that rejects non-INSERT DML, providing a
tamper-evident record for compliance audits.

Implemented in Phase 5 (audit logger) and Phase 6 (RBAC checkpoint).
"""

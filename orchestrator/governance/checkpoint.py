"""
RBACCheckpoint — permission checking and four-eyes enforcement at pipeline gates.

Two responsibilities
--------------------
1. check_permission(user, permission)
   Verify that a CurrentUser holds the required permission.  Raises
   AuthorizationError if not.  Used by the gateway pre-flight (Phase 7) to
   block calls from users who lack the required role for a given operation.

2. request_approval(run_id, gate_name, required_permission, trigger_user, approver_token)
   Resolve the approver's token, check their permission, enforce the four-eyes
   constraint (approver ≠ run trigger), and return the approver's github_login.

   The CLI [y/n] prompt and audit logging are NOT here — they are in HybridGate
   (Phase 3.5).  RBACCheckpoint is responsible only for identity and authority
   verification.  HybridGate orchestrates the full human interaction flow.

Four-eyes principle
-------------------
The user who triggers a run (--token alice_dev_token) cannot approve any gate
within that same run.  This mirrors the SOX change-control requirement: the
developer who writes the change cannot be the sole approver of that change.

Enforcement: approver.github_login != triggered_by (stored in OrchestratorState
and orch_runs).  Violation raises FourEyesViolationError, which the engine treats
as a hard gate failure (the run stays paused until a different approver acts).

Why separate from AuditLogger?
-------------------------------
RBACCheckpoint handles IDENTITY (who are you?) and AUTHORITY (are you allowed?).
AuditLogger handles RECORD (what did you decide?).  HybridGate is the only
component that holds both and writes the audit event after the human decides.
Keeping these separate prevents RBACCheckpoint from becoming a god-object that
knows about database writes.
"""

from orchestrator.gateway.auth import AuthenticationError, CurrentUser, TokenAuthenticator


class AuthorizationError(Exception):
    """Raised when a user lacks the required permission for an operation."""


class FourEyesViolationError(Exception):
    """Raised when the approver is the same person who triggered the run."""


class RBACCheckpoint:
    """Verifies that an approver has the required role and satisfies four-eyes."""

    def __init__(self, authenticator: TokenAuthenticator) -> None:
        self.authenticator = authenticator

    def check_permission(self, user: CurrentUser, permission: str) -> None:
        """Assert that user.permissions contains the required permission.

        Args:
            user:       Resolved CurrentUser from TokenAuthenticator.
            permission: Permission name (e.g. "approve_architecture").

        Raises:
            AuthorizationError: if the permission is not in user.permissions.
        """
        if permission not in user.permissions:
            raise AuthorizationError(
                f"User '{user.github_login}' ({user.role}) does not have "
                f"permission '{permission}'. "
                f"Permissions held: {user.permissions}"
            )

    def verify_four_eyes(self, approver_login: str, triggered_by: str) -> None:
        """Assert that the approver is not the person who triggered the run.

        Args:
            approver_login: github_login of the would-be approver.
            triggered_by:   github_login stored in OrchestratorState / orch_runs.

        Raises:
            FourEyesViolationError: if the two logins are the same.
        """
        if approver_login == triggered_by:
            raise FourEyesViolationError(
                f"Four-eyes violation: '{approver_login}' triggered this run and "
                f"cannot approve their own work. A different user must approve."
            )

    def request_approval(
        self,
        *,
        run_id: str,
        gate_name: str = "",
        required_permission: str,
        trigger_user: str,
        approver_token: str,
    ) -> str:
        """Resolve an approver token, verify authority, enforce four-eyes.

        Called by HybridGate._verify_approver() before the human [y/n] prompt.
        Does NOT prompt the CLI and does NOT write audit events — those are
        HybridGate's responsibilities.

        Args:
            run_id:               FK to orch_runs (for error context only).
            gate_name:            Human-readable gate label for error messages.
            required_permission:  Permission the approver must hold.
            trigger_user:         github_login who started the run (four-eyes check).
            approver_token:       Raw CLI --token of the approving user.

        Returns:
            The approver's github_login (used by HybridGate to record who approved).

        Raises:
            AuthenticationError:   if approver_token is not in users.yaml.
            AuthorizationError:    if approver lacks required_permission.
            FourEyesViolationError: if approver == trigger_user.
        """
        approver: CurrentUser = self.authenticator.resolve(approver_token)
        self.check_permission(approver, required_permission)
        self.verify_four_eyes(approver.github_login, trigger_user)
        return approver.github_login

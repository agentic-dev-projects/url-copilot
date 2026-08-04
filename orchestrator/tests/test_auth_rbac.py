"""
Unit tests for TokenAuthenticator, CurrentUser, and RBACCheckpoint.

No database required — these tests only exercise YAML loading and
permission logic.  Run anywhere:

    .venv/bin/python -m pytest orchestrator/tests/test_auth_rbac.py -v
"""

import pytest

from orchestrator.gateway.auth import (
    AuthenticationError,
    CurrentUser,
    TokenAuthenticator,
)
from orchestrator.governance.checkpoint import (
    AuthorizationError,
    FourEyesViolationError,
    RBACCheckpoint,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def auth():
    """Single TokenAuthenticator for all tests — YAML loaded once."""
    return TokenAuthenticator()


@pytest.fixture(scope="module")
def checkpoint(auth):
    return RBACCheckpoint(auth)


# ── TokenAuthenticator.resolve() ──────────────────────────────────────────────


def test_resolve_alice_returns_correct_user(auth):
    user = auth.resolve("alice_dev_token")
    assert user.github_login == "alice"
    assert user.email == "alice@example.com"
    assert user.role == "DEVELOPER"


def test_resolve_unknown_token_raises_authentication_error(auth):
    with pytest.raises(AuthenticationError):
        auth.resolve("totally_invalid_token")


def test_resolve_builds_current_user_dataclass(auth):
    user = auth.resolve("bob_tl_token")
    assert isinstance(user, CurrentUser)
    assert user.github_login == "bob"


# ── Permission inheritance ────────────────────────────────────────────────────


def test_developer_has_base_permissions(auth):
    user = auth.resolve("alice_dev_token")
    assert "trigger_run" in user.permissions
    assert "view_runs" in user.permissions
    assert "provide_clarification" in user.permissions


def test_developer_cannot_approve_architecture(auth):
    user = auth.resolve("alice_dev_token")
    assert "approve_architecture" not in user.permissions


def test_tech_lead_inherits_developer_permissions(auth):
    user = auth.resolve("bob_tl_token")
    # Own permissions
    assert "approve_architecture" in user.permissions
    assert "approve_schema_change" in user.permissions
    assert "force_stop" in user.permissions
    assert "rollback" in user.permissions
    # Inherited from DEVELOPER
    assert "trigger_run" in user.permissions
    assert "view_runs" in user.permissions
    assert "provide_clarification" in user.permissions


def test_tech_lead_cannot_approve_release(auth):
    user = auth.resolve("bob_tl_token")
    assert "approve_release" not in user.permissions


def test_release_manager_inherits_full_chain(auth):
    user = auth.resolve("carol_rm_token")
    # Has own permission
    assert "approve_release" in user.permissions
    # Inherited from TECH_LEAD
    assert "approve_architecture" in user.permissions
    assert "approve_schema_change" in user.permissions
    # Inherited from DEVELOPER (via TECH_LEAD)
    assert "trigger_run" in user.permissions


def test_release_manager_cannot_manage_users(auth):
    user = auth.resolve("carol_rm_token")
    assert "manage_users" not in user.permissions


def test_admin_has_all_permissions(auth):
    user = auth.resolve("dave_admin_token")
    all_perms = {
        "trigger_run", "view_runs", "provide_clarification",
        "approve_architecture", "approve_schema_change", "force_stop", "rollback",
        "approve_release",
        "manage_users", "emergency_override",
    }
    assert all_perms <= set(user.permissions)


def test_admin_has_unlimited_token_budget(auth):
    user = auth.resolve("dave_admin_token")
    assert user.daily_token_budget == -1


def test_developer_has_finite_token_budget(auth):
    user = auth.resolve("alice_dev_token")
    assert user.daily_token_budget == 50_000


# ── RBACCheckpoint.check_permission() ────────────────────────────────────────


def test_check_permission_passes_for_authorized_user(auth, checkpoint):
    bob = auth.resolve("bob_tl_token")
    checkpoint.check_permission(bob, "approve_architecture")   # should not raise


def test_check_permission_raises_for_unauthorized_user(auth, checkpoint):
    alice = auth.resolve("alice_dev_token")
    with pytest.raises(AuthorizationError, match="approve_architecture"):
        checkpoint.check_permission(alice, "approve_architecture")


def test_check_permission_error_message_includes_role(auth, checkpoint):
    alice = auth.resolve("alice_dev_token")
    with pytest.raises(AuthorizationError) as exc_info:
        checkpoint.check_permission(alice, "approve_release")
    assert "DEVELOPER" in str(exc_info.value)
    assert "alice" in str(exc_info.value)


# ── RBACCheckpoint.verify_four_eyes() ────────────────────────────────────────


def test_verify_four_eyes_passes_for_different_users(checkpoint):
    checkpoint.verify_four_eyes("bob", "alice")   # should not raise


def test_verify_four_eyes_raises_when_same_user(checkpoint):
    with pytest.raises(FourEyesViolationError, match="alice"):
        checkpoint.verify_four_eyes("alice", "alice")


# ── RBACCheckpoint.request_approval() ────────────────────────────────────────


def test_request_approval_returns_approver_login(checkpoint):
    login = checkpoint.request_approval(
        run_id="test-run-001",
        required_permission="approve_architecture",
        trigger_user="alice",
        approver_token="bob_tl_token",
    )
    assert login == "bob"


def test_request_approval_raises_on_bad_token(checkpoint):
    with pytest.raises(AuthenticationError):
        checkpoint.request_approval(
            run_id="test-run-001",
            required_permission="approve_architecture",
            trigger_user="alice",
            approver_token="nonexistent_token",
        )


def test_request_approval_raises_on_insufficient_permission(checkpoint):
    with pytest.raises(AuthorizationError):
        checkpoint.request_approval(
            run_id="test-run-001",
            required_permission="approve_architecture",
            trigger_user="carol",          # approver is alice (DEVELOPER)
            approver_token="alice_dev_token",
        )


def test_request_approval_raises_on_four_eyes_violation(checkpoint):
    # alice tries to approve her own run
    with pytest.raises(FourEyesViolationError):
        checkpoint.request_approval(
            run_id="test-run-001",
            required_permission="trigger_run",   # alice has this permission
            trigger_user="alice",
            approver_token="alice_dev_token",
        )


def test_request_approval_release_manager_can_approve_release(checkpoint):
    login = checkpoint.request_approval(
        run_id="test-run-001",
        required_permission="approve_release",
        trigger_user="alice",
        approver_token="carol_rm_token",
    )
    assert login == "carol"

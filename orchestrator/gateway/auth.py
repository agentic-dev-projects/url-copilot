"""
TokenAuthenticator and CurrentUser — token-based CLI authentication.

How authentication works in this prototype
------------------------------------------
1. Every CLI command passes --token (e.g. alice_dev_token).
2. TokenAuthenticator.resolve(token) looks up the token in users.yaml and
   builds a CurrentUser with the full expanded permission list for that role.
3. All downstream code receives CurrentUser — never the raw token string.
4. The raw token is only ever held by the CLI arg parser and this module.

Why YAML and not a database?
-----------------------------
Users.yaml is the single source of truth for user identity in this prototype.
Maintaining a duplicate orch_users DB table alongside the YAML creates a dual
source-of-truth problem with no compensating benefit.  The TokenAuthenticator
interface (token → CurrentUser) is designed as an Adapter: replacing the YAML
lookup with an OAuth/SSO token introspection call requires changing only this
file.  The rest of the system uses CurrentUser and never sees the mechanism.

Permission expansion
--------------------
Roles form a linear inheritance chain:
    ADMIN → RELEASE_MANAGER → TECH_LEAD → DEVELOPER

_expand_permissions() walks the chain recursively and unions all permission
sets, so CurrentUser.permissions always contains the complete flat list of
everything the user may do.  Callers never need to check inheritance themselves.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).parent.parent / "config"


class AuthenticationError(Exception):
    """Raised when a token is not found in users.yaml."""


@dataclass
class CurrentUser:
    """Resolved identity of the authenticated caller.

    Built once by TokenAuthenticator.resolve() and passed to all downstream
    components.  Permissions are pre-expanded (inheritance already applied).
    """

    github_login: str
    email: str
    role: str
    permissions: list[str] = field(default_factory=list)
    daily_token_budget: int = 50_000    # -1 = unlimited (ADMIN)


class TokenAuthenticator:
    """Resolves CLI tokens to CurrentUser by reading users.yaml + rbac.yaml.

    Both files are loaded once at construction.  If the files change at runtime
    (unusual for a prototype) a new instance must be created.
    """

    def __init__(self) -> None:
        users_path = _CONFIG_DIR / "users.yaml"
        rbac_path = _CONFIG_DIR / "rbac.yaml"

        with users_path.open(encoding="utf-8") as fh:
            self._users: dict = yaml.safe_load(fh)["users"]

        with rbac_path.open(encoding="utf-8") as fh:
            self._rbac: dict = yaml.safe_load(fh)["roles"]

    def resolve(self, token: str) -> CurrentUser:
        """Resolve a token string to a fully-populated CurrentUser.

        Args:
            token: The raw --token value from the CLI.

        Returns:
            CurrentUser with github_login, email, role, and expanded permissions.

        Raises:
            AuthenticationError: if the token is not in users.yaml.
        """
        profile = self._users.get(token)
        if profile is None:
            raise AuthenticationError(
                f"Token not recognised. Check --token value or update users.yaml."
            )

        role = profile["role"]
        permissions = self._expand_permissions(role)
        budget = self._rbac[role].get("daily_token_budget", 50_000)

        return CurrentUser(
            github_login=profile["github_login"],
            email=profile["email"],
            role=role,
            permissions=permissions,
            daily_token_budget=budget,
        )

    # ── private ───────────────────────────────────────────────────────────────

    def _expand_permissions(self, role: str) -> list[str]:
        """Return the full permission list for a role, including all inherited permissions.

        Walks the 'inherits' chain recursively and unions all permission sets.
        Result is returned as a sorted list for deterministic behaviour.

        Example:
            TECH_LEAD inherits DEVELOPER
            _expand_permissions("TECH_LEAD")
            → sorted(DEVELOPER.permissions ∪ TECH_LEAD.permissions)
        """
        role_config = self._rbac.get(role, {})
        own: set[str] = set(role_config.get("permissions", []))
        parent: str | None = role_config.get("inherits")
        if parent:
            own |= set(self._expand_permissions(parent))
        return sorted(own)

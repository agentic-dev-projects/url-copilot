"""
orchestrator.config — Runtime configuration files (YAML).

Files
-----
users.yaml      Mock auth token registry.  Maps token strings to user profiles
                (github_login, email, role).  In production, replace the YAML
                lookup in TokenAuthenticator with an OAuth/SSO API call.

rbac.yaml       Role definitions: permissions list and daily token budget per role.
                Supports role inheritance (TECH_LEAD inherits all DEVELOPER permissions).

models.yaml     Per-stage LLM selection.  Cheap stages (classification, docs) use
                gpt-4o-mini; reasoning-heavy stages (architecture, implementation)
                use gpt-4o.  Change the model without touching code.

evaluator.yaml  Hybrid evaluator settings: which stages receive AI evaluation,
                which model runs the validator (o1-mini), and which critic prompt
                file to use per stage.

These files are read at runtime, not at import time.  They are versioned in git,
so every config change is auditable.
"""

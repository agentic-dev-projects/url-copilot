"""
TokenBudgetManager — enforces per-role daily token caps using orch_metrics.

Why orch_metrics instead of LangSmith?
----------------------------------------
LangSmith automatically tracks token usage per LLM call via wrap_openai().
However, TokenBudgetManager must make a real-time enforcement decision BEFORE
each LLM call — it cannot wait for LangSmith's async trace to be recorded.

Querying our own PostgreSQL orch_metrics table is sub-millisecond.  Calling
the LangSmith API for budget enforcement would add network latency and a hard
dependency on an external SaaS to every single LLM call.  orch_metrics is the
authoritative source for budget decisions; LangSmith is the observability surface.

Budget lookup query
--------------------
orch_metrics does not store github_login directly.  It stores run_id, which
is a FK to orch_runs.  orch_runs stores triggered_by (github_login).
So today's token count for a user is:

    SELECT COALESCE(SUM(tokens_in + tokens_out), 0)
    FROM orch_metrics m
    JOIN orch_runs r ON m.run_id = r.id
    WHERE r.triggered_by = :login
      AND DATE(m.created_at AT TIME ZONE 'UTC') = CURRENT_DATE

Unlimited budget
-----------------
ADMIN users have daily_token_budget = -1.  check() short-circuits immediately
for these users — no DB query needed.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.gateway.auth import CurrentUser
from orchestrator.gateway.models import TokenBudgetExceededError


class TokenBudgetManager:
    """Checks and enforces per-role daily token budgets via orch_metrics."""

    def check(self, user: CurrentUser, session: Session, estimated_tokens: int = 0) -> None:
        """Assert the user has not exceeded their daily token budget.

        Args:
            user:             Authenticated CurrentUser with daily_token_budget.
            session:          SQLAlchemy session — used for the orch_metrics query.
            estimated_tokens: Rough estimate of tokens this request will consume
                              (prompt character count // 4).  Used to give a
                              more accurate "would exceed" projection.

        Raises:
            TokenBudgetExceededError: if today's usage + estimated_tokens
                                      exceeds user.daily_token_budget.
        """
        if user.daily_token_budget == -1:
            return      # ADMIN — unlimited

        row = session.execute(
            text(
                "SELECT COALESCE(SUM(m.tokens_in + m.tokens_out), 0) AS used "
                "FROM orch_metrics m "
                "JOIN orch_runs r ON m.run_id = r.id "
                "WHERE r.triggered_by = :login "
                "  AND DATE(m.created_at AT TIME ZONE 'UTC') = CURRENT_DATE"
            ),
            {"login": user.github_login},
        ).mappings().one()

        used: int = int(row["used"])
        projected = used + estimated_tokens

        if projected > user.daily_token_budget:
            raise TokenBudgetExceededError(
                f"Daily token budget exceeded for '{user.github_login}' "
                f"({user.role}): used {used:,} + estimated {estimated_tokens:,} "
                f"= {projected:,} > limit {user.daily_token_budget:,}. "
                f"Budget resets at midnight UTC."
            )

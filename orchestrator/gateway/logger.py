"""
StructuredLogger — JSON log lines to stdout for gateway-level events.

Scope: gateway-level events only — auth failures, guardrail violations,
rate limit hits, request/response metadata.

LLM-level events (inputs, outputs, token counts, cost, latency) are handled
automatically by LangSmith via wrap_openai().  StructuredLogger does NOT
duplicate those — it would just add noise.

Why JSON to stdout?
--------------------
Stdout JSON is the universal log format for containerised services.  In
production, a log aggregator (Datadog, CloudWatch, etc.) picks up stdout
and indexes the structured fields.  In development, `python -m orchestrator.run
... | jq` gives instant structured output.
"""

import json
import sys
from datetime import datetime, timezone
from typing import Any

from orchestrator.gateway.models import GatewayRequest, GatewayResponse


class StructuredLogger:
    """Writes one-line JSON log events to stdout."""

    def log_request(
        self,
        trace_id: str,
        github_login: str,
        role: str,
        request: GatewayRequest,
    ) -> None:
        """Log an inbound gateway request (before the LLM call)."""
        self._emit(
            level="INFO",
            event="gateway.request",
            trace_id=trace_id,
            actor=github_login,
            role=role,
            run_id=request.run_id,
            stage_name=request.stage_name,
            model=request.model,
            prompt_version=request.prompt_version,
            message_count=len(request.messages),
        )

    def log_response(
        self,
        trace_id: str,
        response: GatewayResponse,
        duration_ms: float,
    ) -> None:
        """Log a completed gateway response (after the LLM call)."""
        self._emit(
            level="INFO",
            event="gateway.response",
            trace_id=trace_id,
            cache_hit=response.cache_hit,
            has_tool_calls=bool(response.tool_calls),
            tokens_in=response.usage.get("input_tokens", 0),
            tokens_out=response.usage.get("output_tokens", 0),
            duration_ms=round(duration_ms, 1),
        )

    def log_error(self, trace_id: str, error_type: str, message: str, **extra: Any) -> None:
        """Log a gateway-level error (auth failure, guardrail violation, etc.)."""
        self._emit(
            level="ERROR",
            event=f"gateway.{error_type}",
            trace_id=trace_id,
            message=message,
            **extra,
        )

    # ── private ───────────────────────────────────────────────────────────────

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            **fields,
        }
        print(json.dumps(record), file=sys.stdout, flush=True)

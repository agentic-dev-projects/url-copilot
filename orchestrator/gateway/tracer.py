"""
RequestTracer — generates a trace_id UUID and measures call latency.

trace_id is the correlation key between:
  - The orch_metrics row written by CostTracker
  - The LangSmith trace (LangSmith uses its own internal IDs, but the trace_id
    is included in the StructuredLogger JSON so the two can be correlated)
  - Gateway log lines written by StructuredLogger

Why not use LangSmith's trace ID directly?
-------------------------------------------
LangSmith's trace IDs are generated asynchronously inside the wrapped client.
RequestTracer generates the trace_id synchronously before the LLM call so it
is available as a key in orch_metrics regardless of whether LangSmith tracing
is enabled or the LangSmith API is reachable.
"""

import time
import uuid


class RequestTracer:
    """Generates trace_ids and measures wall-clock latency for LLM calls."""

    def __init__(self) -> None:
        self._spans: dict[str, float] = {}      # trace_id → start_time (epoch seconds)

    def start(self) -> str:
        """Generate a new trace_id UUID and start a timing span.

        Returns:
            trace_id: UUID4 string, used as the correlation key across orch_metrics
                      and gateway log lines.
        """
        trace_id = str(uuid.uuid4())
        self._spans[trace_id] = time.monotonic()
        return trace_id

    def end(self, trace_id: str) -> float:
        """End the timing span and return the elapsed duration in milliseconds.

        Args:
            trace_id: The same ID returned by start().

        Returns:
            Duration in milliseconds (float).  Returns 0.0 if trace_id is unknown.
        """
        start = self._spans.pop(trace_id, None)
        if start is None:
            return 0.0
        return (time.monotonic() - start) * 1000

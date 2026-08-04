"""
pipeline_logger.py — Production-grade structured logging for the orchestrator.

Design
------
Every meaningful lifecycle event in the pipeline is emitted through
PipelineLogger.  Each event is a typed method call that captures exactly
the fields relevant to that event.  Internally each call emits a JSON-encoded
log record so any downstream log aggregator (Datadog, CloudWatch, Splunk) can
parse it without configuration.

Two output formats are configured by setup_logging():

  Console (stderr)
    Human-readable, color-coded lines designed to let an operator watch the
    pipeline run in real-time and understand at a glance:
      - who triggered the action (actor + role)
      - which stage is running
      - what tools were called and what they returned
      - gate decisions (approved/rejected, by whom)
      - success ✓ / failure ✗ / waiting 🔐 at a glance

  File (orchestrator.log, JSON-per-line)
    Machine-parseable.  One JSON object per line, every field named.
    Suitable for log aggregators, alerting, and post-hoc debugging.

Usage
-----
    from orchestrator.logging import PipelineLogger, setup_logging

    setup_logging()   # call once at process startup (run.py does this)

    log = PipelineLogger(run_id="orch-abc123", actor="alice", role="DEVELOPER")
    log.run_started(requirement="Add QR code endpoint")
    log.stage_started("requirements_analysis")
    log.tool_called("requirements_analysis", "read_file", path="service/main.py")
    log.tool_completed("requirements_analysis", "read_file", result="142 bytes", latency_ms=12.3)
    log.stage_completed("requirements_analysis", duration_ms=1800, next_stage="architecture_design")
    log.gate_reached("architecture_gate", required_permission="approve_architecture")
    log.gate_approved("architecture_gate", approver="bob", approver_role="TECH_LEAD", comment="lgtm")
    log.run_completed(duration_ms=386000, stages_done=9, stages_failed=0, pr_url="https://...")
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── ANSI colours ─────────────────────────────────────────────────────────────
_R  = "\033[0m"   # reset
_B  = "\033[1m"   # bold
_D  = "\033[2m"   # dim
_CY = "\033[36m"  # cyan   — stages
_GR = "\033[32m"  # green  — success
_YE = "\033[33m"  # yellow — gates / warnings
_RE = "\033[31m"  # red    — errors / failures
_MA = "\033[35m"  # magenta — tools
_BL = "\033[34m"  # blue   — run lifecycle
_WH = "\033[37m"  # white  — default

# Category label → (color, icon)
_STYLES: dict[str, tuple[str, str]] = {
    "RUN":   (_BL, "▶"),
    "STAGE": (_CY, "▷"),
    "GATE":  (_YE, "🔐"),
    "TOOL":  (_MA, "⚙"),
    "GIT":   (_D,  "⬆"),
    "PR":    (_GR, "⎇"),
    "ERROR": (_RE, "✗"),
}


# ── Console formatter ─────────────────────────────────────────────────────────


class _ConsoleFormatter(logging.Formatter):
    """Renders JSON log records as colored, human-readable terminal lines."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            data: dict = json.loads(record.getMessage())
        except (json.JSONDecodeError, TypeError):
            return record.getMessage()

        ts    = datetime.fromisoformat(data.get("ts", "")).strftime("%H:%M:%S")
        event = data.get("event", "")
        cat   = event.split(".")[0].upper() if "." in event else "INFO"
        color, icon = _STYLES.get(cat, (_WH, "·"))

        status_icon = _status_icon(event)
        category    = f"{cat:<5}"
        details     = _render_details(event, data)

        return (
            f"{_D}{ts}{_R} "
            f"{color}{status_icon} {_B}{category}{_R}{color}│{_R} "
            f"{details}"
        )


def _status_icon(event: str) -> str:
    if event.endswith(".completed") or event.endswith(".approved") or event.endswith(".created"):
        return f"{_GR}✓{_R}"
    if event.endswith(".failed") or event.endswith(".rejected") or event.endswith(".error"):
        return f"{_RE}✗{_R}"
    if event == "gate.reached":
        return f"{_YE}⏸{_R}"
    return " "


def _render_details(event: str, d: dict) -> str:
    """Build the human-readable detail string for each event type."""
    run_id  = d.get("run_id", "")
    stage   = d.get("stage_name", "")
    actor   = d.get("actor", "")
    role    = d.get("role", "")

    def _dur(ms: float) -> str:
        if ms < 1000:
            return f"{ms:.0f}ms"
        if ms < 60_000:
            return f"{ms/1000:.1f}s"
        return f"{int(ms/60000)}m{int((ms % 60000)/1000)}s"

    # ── RUN ──────────────────────────────────────────────────────────────────
    if event == "run.started":
        req = d.get("requirement", "")[:70]
        return (
            f"{_BL}{_B}{run_id}{_R}  "
            f"actor={_B}{actor}{_R}({role})  "
            f"{_D}\"{req}\"{_R}"
        )

    if event == "run.completed":
        done   = d.get("stages_done", "?")
        failed = d.get("stages_failed", 0)
        dur    = _dur(d.get("duration_ms", 0))
        cost   = d.get("cost_usd", "")
        pr_url = d.get("pr_url", "")
        fail_part = f"  {_RE}failed={failed}{_R}" if failed else ""
        pr_part   = f"  PR → {_GR}{pr_url}{_R}" if pr_url else f"  {_YE}PR=MISSING{_R}"
        cost_part = f"  cost=${cost}" if cost else ""
        return (
            f"{_BL}{_B}{run_id}{_R}  COMPLETED  "
            f"duration={dur}  stages={done}/9{fail_part}{pr_part}{cost_part}"
        )

    if event == "run.failed":
        err = d.get("error", "")[:120]
        return f"{_RE}{_B}{run_id}{_R}  FAILED  {err}"

    # ── STAGE ─────────────────────────────────────────────────────────────────
    if event == "stage.started":
        prior = d.get("prior_stages", [])
        parallel = f"  {_D}[parallel]{_R}" if d.get("parallel") else ""
        prior_part = f"  prior=[{', '.join(prior)}]" if prior else ""
        return f"{_CY}{_B}{stage}{_R}  run={run_id}{prior_part}{parallel}"

    if event == "stage.completed":
        dur    = _dur(d.get("duration_ms", 0))
        next_s = d.get("next_stage", "")
        extra  = _stage_extra(d)
        nxt    = f"  → {_D}{next_s}{_R}" if next_s else ""
        return f"{_GR}{_B}{stage}{_R}  {dur}{extra}{nxt}"

    if event == "stage.failed":
        dur = _dur(d.get("duration_ms", 0))
        err = d.get("error", "")[:120]
        return f"{_RE}{_B}{stage}{_R}  {dur}  {_RE}{err}{_R}"

    if event == "stage.cache_hit":
        return f"{_D}{stage}  CACHE HIT — LLM call skipped{_R}"

    # ── GATE ──────────────────────────────────────────────────────────────────
    if event == "gate.reached":
        gate    = d.get("gate_name", "")
        perm    = d.get("required_permission", "")
        by      = d.get("triggered_by", "")
        return (
            f"{_YE}{_B}{gate}{_R}  "
            f"WAITING  needs={_B}{perm}{_R}  triggered_by={by}"
        )

    if event == "gate.approved":
        gate     = d.get("gate_name", "")
        approver = d.get("approver", "")
        arole    = d.get("approver_role", "")
        comment  = d.get("comment", "")
        cmt = f"  {_D}\"{comment}\"{_R}" if comment else ""
        return f"{_GR}{_B}{gate}{_R}  APPROVED  by={_B}{approver}{_R}({arole}){cmt}"

    if event == "gate.rejected":
        gate   = d.get("gate_name", "")
        by     = d.get("approver", "")
        reason = d.get("comment", "")
        return f"{_RE}{_B}{gate}{_R}  REJECTED  by={by}  reason={reason}"

    # ── TOOL ──────────────────────────────────────────────────────────────────
    if event == "tool.called":
        tool    = d.get("tool_name", "")
        summary = d.get("args_summary", "")
        return f"{_MA}{stage}/{_B}{tool}{_R}  {_D}→  {summary}{_R}"

    if event == "tool.completed":
        tool    = d.get("tool_name", "")
        result  = d.get("result_summary", "")
        lat     = d.get("latency_ms", 0)
        return f"{_MA}{stage}/{_B}{tool}{_R}  {result}  {_D}({lat:.0f}ms){_R}"

    if event == "tool.error":
        tool = d.get("tool_name", "")
        err  = d.get("error", "")[:120]
        return f"{_RE}{stage}/{_B}{tool}{_R}  ERROR: {err}"

    # ── GIT / PR ──────────────────────────────────────────────────────────────
    if event == "git.staged":
        files = d.get("files", [])
        n     = len(files)
        flist = ", ".join(files[:3]) + (" ..." if n > 3 else "")
        return f"{_D}staged {n} file(s): {flist}{_R}"

    if event == "git.committed":
        return (
            f"{_D}committed sha={d.get('sha', '')}  "
            f"branch={d.get('branch_name', '')}{_R}"
        )

    if event == "git.pushed":
        return (
            f"pushed sha={_B}{d.get('sha', '')}{_R}  "
            f"→ {d.get('branch_name', '')}"
        )

    if event == "git.cleanup":
        return f"{_D}cleaned local state — working tree restored{_R}"

    if event == "pr.created":
        return (
            f"PR #{_B}{d.get('pr_number')}{_R}  "
            f"{_GR}{d.get('pr_url', '')}{_R}"
        )

    if event == "pr.error":
        return f"{_RE}PR creation failed: {d.get('error', '')}{_R}"

    # ── fallback ──────────────────────────────────────────────────────────────
    skip = {"ts", "level", "event", "run_id", "actor", "role"}
    parts = [f"{k}={v}" for k, v in d.items() if k not in skip and v is not None]
    return "  ".join(parts) or event


def _stage_extra(d: dict) -> str:
    """Append stage-specific completion details after the duration."""
    stage = d.get("stage_name", "")
    parts: list[str] = []

    if stage == "implementation":
        branch = d.get("branch_name")
        pr_url = d.get("pr_url")
        pr_num = d.get("pr_number")
        if branch:
            parts.append(f"branch={branch}")
        if pr_url:
            parts.append(f"{_GR}PR=#{pr_num} ✅{_R}")
        elif branch:
            parts.append(f"{_YE}PR=MISSING ⚠{_R}")

    elif stage in ("unit_tests", "integration_tests"):
        passed = d.get("passed", 0)
        failed = d.get("failed", 0)
        color  = _RE if failed else _GR
        parts.append(f"{color}passed={passed}  failed={failed}{_R}")

    elif stage == "release_readiness":
        if d.get("ready_to_ship"):
            parts.append(f"{_GR}READY ✅{_R}")
        else:
            parts.append(f"{_YE}NOT READY ⚠{_R}")

    return ("  " + "  ".join(parts)) if parts else ""


# ── JSON formatter ────────────────────────────────────────────────────────────


class _JsonFormatter(logging.Formatter):
    """Emits the raw JSON record as-is (one JSON object per line)."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


# ── Plain-text formatter ──────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _PlainFormatter(logging.Formatter):
    """Same layout as _ConsoleFormatter but with ANSI colour codes stripped."""

    _console = _ConsoleFormatter()

    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_RE.sub("", self._console.format(record))


# ── Stdout tee ────────────────────────────────────────────────────────────────


class _Tee:
    """Forwards every write() to both the original stream and a log file.

    Assigned to sys.stdout so that print() calls land in both the terminal
    and orchestrator_app.log without changing any call sites.
    """

    def __init__(self, original_stream, log_path: str) -> None:
        self._orig = original_stream
        self._file = open(log_path, "a", encoding="utf-8", buffering=1)

    def write(self, data: str) -> int:
        self._orig.write(data)
        self._file.write(data)
        return len(data)

    def flush(self) -> None:
        self._orig.flush()
        self._file.flush()

    def __getattr__(self, name: str):
        return getattr(self._orig, name)


# ── setup_logging ─────────────────────────────────────────────────────────────


def setup_logging(
    log_file: str = "orchestrator.log",
    app_log_file: str = "orchestrator_app.log",
    level: int = logging.INFO,
) -> None:
    """Configure the orchestrator logging system — file-only, no console noise.

    Two log files are written in the current working directory:

      orchestrator_app.log  — human-readable, one line per event (no ANSI) +
                              all CLI print() output.  Read with:
                                tail -f orchestrator_app.log

      orchestrator.log      — machine-parseable JSON, one object per line.
                              Query with: jq . orchestrator.log

    sys.stdout is tee'd so print() output goes to BOTH the terminal and
    orchestrator_app.log.  interactive input() prompts still appear on the
    terminal as normal.

    Third-party loggers (openai, httpx, urllib3, etc.) are kept at WARNING
    and routed to orchestrator_app.log so unexpected errors are captured.
    """
    root = logging.getLogger()
    root.setLevel(logging.WARNING)

    orch_logger = logging.getLogger("orchestrator")
    orch_logger.setLevel(level)
    orch_logger.propagate = False

    try:
        # Tee stdout → terminal + orchestrator_app.log
        sys.stdout = _Tee(sys.stdout, app_log_file)

        # Human-readable plain-text log events
        app_handler = logging.FileHandler(app_log_file, encoding="utf-8", mode="a")
        app_handler.setFormatter(_PlainFormatter())
        app_handler.setLevel(level)
        orch_logger.addHandler(app_handler)

        # Route WARNING+ third-party logs to the same readable file
        root_handler = logging.FileHandler(app_log_file, encoding="utf-8", mode="a")
        root_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
        ))
        root_handler.setLevel(logging.WARNING)
        root.addHandler(root_handler)

        # Machine-readable JSON file
        json_handler = logging.FileHandler(log_file, encoding="utf-8")
        json_handler.setFormatter(_JsonFormatter())
        json_handler.setLevel(logging.DEBUG)
        orch_logger.addHandler(json_handler)

    except OSError:
        pass  # non-fatal on read-only filesystems

    except OSError:
        pass  # non-fatal on read-only filesystems


# ── PipelineLogger ────────────────────────────────────────────────────────────


class PipelineLogger:
    """Emits structured pipeline lifecycle events.

    All methods emit a JSON-encoded log record with a consistent base envelope:
      { ts, level, event, run_id, actor, role, ...event-specific fields }

    Instantiate one PipelineLogger per run.  Pass it to nodes, agents, and
    GitHub tools that need to emit events.
    """

    _logger = logging.getLogger("orchestrator.pipeline")

    def __init__(self, run_id: str, actor: str, role: str) -> None:
        self._run_id = run_id
        self._actor  = actor
        self._role   = role

    # ── helpers ───────────────────────────────────────────────────────────────

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        record: dict = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "level":  level,
            "event":  event,
            "run_id": self._run_id,
            "actor":  self._actor,
            "role":   self._role,
            **{k: v for k, v in kwargs.items() if v is not None},
        }
        getattr(self._logger, level.lower())(json.dumps(record, default=str))

    # ── Run lifecycle ─────────────────────────────────────────────────────────

    def run_started(self, requirement: str, scenario: str) -> None:
        self._emit("info", "run.started", requirement=requirement, scenario=scenario)

    def run_completed(
        self,
        duration_ms: float,
        stages_done: int,
        stages_failed: int,
        pr_url: str | None = None,
        cost_usd: float | None = None,
    ) -> None:
        self._emit(
            "info", "run.completed",
            duration_ms=round(duration_ms),
            stages_done=stages_done,
            stages_failed=stages_failed,
            pr_url=pr_url,
            cost_usd=f"{cost_usd:.4f}" if cost_usd else None,
        )

    def run_failed(self, error: str) -> None:
        self._emit("error", "run.failed", error=error)

    # ── Stage lifecycle ───────────────────────────────────────────────────────

    def stage_started(
        self,
        stage_name: str,
        prior_stages: list[str] | None = None,
        parallel: bool = False,
    ) -> None:
        self._emit(
            "info", "stage.started",
            stage_name=stage_name,
            prior_stages=prior_stages or [],
            parallel=parallel or None,
        )

    def stage_completed(
        self,
        stage_name: str,
        duration_ms: float,
        next_stage: str | None = None,
        **extra: Any,
    ) -> None:
        self._emit(
            "info", "stage.completed",
            stage_name=stage_name,
            duration_ms=round(duration_ms),
            next_stage=next_stage,
            **extra,
        )

    def stage_failed(
        self,
        stage_name: str,
        error: str,
        duration_ms: float = 0,
    ) -> None:
        self._emit(
            "error", "stage.failed",
            stage_name=stage_name,
            error=error,
            duration_ms=round(duration_ms),
        )

    def stage_cache_hit(self, stage_name: str) -> None:
        self._emit("info", "stage.cache_hit", stage_name=stage_name)

    # ── Gate lifecycle ────────────────────────────────────────────────────────

    def gate_reached(
        self,
        gate_name: str,
        required_permission: str,
        triggered_by: str,
    ) -> None:
        self._emit(
            "warning", "gate.reached",
            gate_name=gate_name,
            required_permission=required_permission,
            triggered_by=triggered_by,
        )

    def gate_approved(
        self,
        gate_name: str,
        approver: str,
        approver_role: str,
        comment: str = "",
    ) -> None:
        self._emit(
            "info", "gate.approved",
            gate_name=gate_name,
            approver=approver,
            approver_role=approver_role,
            comment=comment or None,
        )

    def gate_rejected(
        self,
        gate_name: str,
        approver: str,
        approver_role: str,
        comment: str = "",
    ) -> None:
        self._emit(
            "warning", "gate.rejected",
            gate_name=gate_name,
            approver=approver,
            approver_role=approver_role,
            comment=comment or None,
        )

    # ── Tool lifecycle ────────────────────────────────────────────────────────

    def tool_called(
        self,
        stage_name: str,
        tool_name: str,
        args_summary: str,
    ) -> None:
        self._emit(
            "info", "tool.called",
            stage_name=stage_name,
            tool_name=tool_name,
            args_summary=args_summary,
        )

    def tool_completed(
        self,
        stage_name: str,
        tool_name: str,
        result_summary: str,
        latency_ms: float = 0,
    ) -> None:
        self._emit(
            "info", "tool.completed",
            stage_name=stage_name,
            tool_name=tool_name,
            result_summary=result_summary,
            latency_ms=round(latency_ms, 1),
        )

    def tool_error(
        self,
        stage_name: str,
        tool_name: str,
        error: str,
    ) -> None:
        self._emit(
            "error", "tool.error",
            stage_name=stage_name,
            tool_name=tool_name,
            error=error,
        )

    # ── Git / PR events ───────────────────────────────────────────────────────

    def git_staged(self, branch_name: str, files: list[str]) -> None:
        self._emit("info", "git.staged", branch_name=branch_name, files=files)

    def git_committed(self, branch_name: str, sha: str) -> None:
        self._emit("info", "git.committed", branch_name=branch_name, sha=sha)

    def git_pushed(self, branch_name: str, sha: str) -> None:
        self._emit("info", "git.pushed", branch_name=branch_name, sha=sha)

    def git_cleanup(self, branch_name: str) -> None:
        self._emit("info", "git.cleanup", branch_name=branch_name)

    def pr_created(self, branch_name: str, pr_number: int, pr_url: str) -> None:
        self._emit(
            "info", "pr.created",
            branch_name=branch_name,
            pr_number=pr_number,
            pr_url=pr_url,
        )

    def pr_error(self, branch_name: str, error: str) -> None:
        self._emit("error", "pr.error", branch_name=branch_name, error=error)

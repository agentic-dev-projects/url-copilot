"""
test_runner.py — run pytest and flake8 against the service layer.

Both functions spawn a subprocess against service/ so results reflect the
actual filesystem state at call time — no caching is appropriate here.
The stage agent (Phase 12) calls run_tests() after write_file() to verify
the generated code passes before committing.

Output parsing
--------------
pytest -v --tb=short writes a summary line: "X passed, Y failed in Z.ZZs".
We parse that line rather than the full output so the result dict stays small
enough to fit in an LLM context window (the full output is also returned for
the agent to reason about specific failures).

flake8 --format=default writes one violation per line:
  path/to/file.py:line:col: Ecode message
We return all violations as a list of strings so the agent can request targeted
fixes.
"""

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


def run_tests(path: str = "service/tests/") -> dict:
    """Run pytest against path and return structured results.

    Args:
        path: Path to test directory or file, relative to PROJECT_ROOT.
              Defaults to "service/tests/" — all service-layer tests.

    Returns:
        {
          "passed":  int,   — number of passing tests
          "failed":  int,   — number of failing tests
          "errors":  int,   — number of collection/error items
          "output":  str,   — full pytest stdout+stderr (for agent reasoning)
          "success": bool,  — True iff failed == 0 and errors == 0
        }
    """
    result = subprocess.run(
        [
            "python", "-m", "pytest", path,
            "-v", "--tb=short", "--no-header", "-q",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    output = result.stdout + result.stderr
    passed, failed, errors = _parse_pytest_summary(output)
    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "output": output,
        "success": failed == 0 and errors == 0,
    }


def run_linter(path: str = "service/") -> dict:
    """Run flake8 against path and return structured results.

    Args:
        path: Directory or file to lint, relative to PROJECT_ROOT.
              Defaults to "service/" — the entire service layer.

    Returns:
        {
          "passed":     bool,       — True iff no violations found
          "violations": list[str],  — one violation string per line
          "count":      int,        — total number of violations
        }
    """
    result = subprocess.run(
        [
            "python", "-m", "flake8", path,
            "--max-line-length=120",
            "--extend-ignore=E501",   # line-length handled by --max-line-length
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    violations = [v for v in result.stdout.splitlines() if v.strip()]
    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "count": len(violations),
    }


# ── private ───────────────────────────────────────────────────────────────────


def _parse_pytest_summary(output: str) -> tuple[int, int, int]:
    """Extract passed/failed/error counts from pytest summary line.

    pytest -q writes a line like: "12 passed, 3 failed, 1 error in 0.45s"
    Counts default to 0 when not present (e.g. "5 passed" → failed=0, errors=0).
    """
    passed = _extract_count(output, "passed")
    failed = _extract_count(output, "failed")
    errors = _extract_count(output, "error")
    return passed, failed, errors


def _extract_count(text: str, label: str) -> int:
    match = re.search(rf"(\d+)\s+{label}", text)
    return int(match.group(1)) if match else 0

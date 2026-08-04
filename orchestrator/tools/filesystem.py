"""
filesystem.py — file read/write/search tools for stage agents.

Write safety
------------
read_file() and search_codebase() can access any path under PROJECT_ROOT.
write_file() is restricted to the service/ directory — stage agents must not
modify orchestrator code, config files, or anything outside the service layer.
The check resolves symlinks before comparing prefixes, so path traversal
attempts like "service/../../orchestrator/gateway.py" are blocked.

PROJECT_ROOT is resolved at import time so it is stable regardless of the
process working directory when the tool is called.
"""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
SERVICE_DIR = PROJECT_ROOT / "service"
REQUIREMENTS_TXT = PROJECT_ROOT / "requirements.txt"

# Paths outside service/ that agents are explicitly allowed to write
_ALLOWED_ROOT_FILES = frozenset({str(REQUIREMENTS_TXT)})

# Files inside service/ that must never be overwritten by stage agents.
# These are the original test suites — the LLM must write to new files
# (e.g. test_qr_endpoint.py) rather than stomping on existing coverage.
_READONLY_PATHS = frozenset({
    str(SERVICE_DIR / "tests" / "unit"        / "test_urls.py"),
    str(SERVICE_DIR / "tests" / "integration" / "test_urls.py"),
    str(SERVICE_DIR / "tests" / "unit"        / "test_url_service.py"),
    str(SERVICE_DIR / "tests" / "unit"        / "test_security.py"),
    str(SERVICE_DIR / "tests" / "unit"        / "test_url.py"),
    str(SERVICE_DIR / "tests" / "unit"        / "test_url_generator.py"),
    str(SERVICE_DIR / "tests" / "integration" / "test_auth.py"),
})


class WriteGuardrailError(Exception):
    """Raised when write_file() is asked to write outside service/."""


def read_file(path: str) -> str:
    """Return the contents of a file, resolved relative to PROJECT_ROOT.

    Args:
        path: Path relative to the project root (e.g. "service/main.py").

    Returns:
        File contents as a UTF-8 string.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if the resolved path escapes PROJECT_ROOT.
    """
    resolved = (PROJECT_ROOT / path).resolve()
    _assert_within(resolved, PROJECT_ROOT, "read")
    return resolved.read_text(encoding="utf-8")


def write_file(path: str, content: str) -> bool:
    """Write content to a file inside service/ or to requirements.txt.

    Creates parent directories as needed.

    Args:
        path:    Path relative to the project root — must be under service/
                 or exactly "requirements.txt".
        content: UTF-8 string to write.

    Returns:
        True on success.

    Raises:
        WriteGuardrailError: if the resolved path is outside the allowed paths.
    """
    resolved = (PROJECT_ROOT / path).resolve()
    in_service = str(resolved).startswith(str(SERVICE_DIR))
    in_allowlist = str(resolved) in _ALLOWED_ROOT_FILES
    if not in_service and not in_allowlist:
        raise WriteGuardrailError(
            f"write_file: path '{path}' resolves to '{resolved}' which is "
            f"outside service/ — write not permitted. "
            f"Only service/ files and requirements.txt may be written."
        )
    if str(resolved) in _READONLY_PATHS:
        raise WriteGuardrailError(
            f"write_file: '{path}' is a protected file and cannot be overwritten. "
            f"Write your new tests to a different filename "
            f"(e.g. test_qr_endpoint.py or test_<feature>_integration.py)."
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return True


def list_directory(path: str) -> list[str]:
    """Return names of entries in a directory, relative to PROJECT_ROOT.

    Args:
        path: Directory path relative to project root.

    Returns:
        Sorted list of entry names (files and subdirectories).

    Raises:
        NotADirectoryError: if path is not a directory.
        ValueError: if the resolved path escapes PROJECT_ROOT.
    """
    resolved = (PROJECT_ROOT / path).resolve()
    _assert_within(resolved, PROJECT_ROOT, "list")
    if not resolved.is_dir():
        raise NotADirectoryError(f"list_directory: '{path}' is not a directory")
    return sorted(e.name for e in resolved.iterdir())


def search_codebase(query: str) -> list[dict]:
    """Search for a string across all .py files under service/.

    Runs grep -rn under service/ and returns structured matches.  Returns an
    empty list if grep finds nothing (exit code 1) or if grep is unavailable.

    Args:
        query: The literal string to search for.

    Returns:
        List of {"file": str, "line": int, "content": str} dicts, where
        "file" is relative to PROJECT_ROOT.
    """
    result = subprocess.run(
        ["grep", "-rn", query, "service/", "--include=*.py"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    matches: list[dict] = []
    for raw_line in result.stdout.splitlines():
        # grep -n output format: "path/to/file.py:42:    content here"
        parts = raw_line.split(":", 2)
        if len(parts) == 3:
            file_path, line_num, content = parts
            try:
                matches.append(
                    {
                        "file": file_path,
                        "line": int(line_num),
                        "content": content,
                    }
                )
            except ValueError:
                continue
    return matches


# ── private ───────────────────────────────────────────────────────────────────


def _assert_within(resolved: Path, root: Path, op: str) -> None:
    if not str(resolved).startswith(str(root)):
        raise ValueError(
            f"{op}_file: resolved path '{resolved}' escapes project root '{root}'"
        )

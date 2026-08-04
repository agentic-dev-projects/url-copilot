"""
Unit tests for the Phase 10 Tool Registry.

No network calls, no GitHub API — github_client functions are mocked.
filesystem tests use pytest's tmp_path fixture for write tests.
test_runner tests run against the real service/tests/ suite.
registry tests verify dispatch, caching, schema shape, and error handling.

Run: .venv/bin/python -m pytest orchestrator/tests/test_tools.py -v
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.cache.tool_cache import ToolCache
from orchestrator.tools.filesystem import (
    PROJECT_ROOT,
    WriteGuardrailError,
    list_directory,
    read_file,
    search_codebase,
    write_file,
)
from orchestrator.tools.registry import TOOL_SCHEMAS, TOOLS, ToolRegistry
from orchestrator.tools.test_runner import _parse_pytest_summary, run_linter, run_tests


# ── filesystem: read_file ─────────────────────────────────────────────────────


def test_read_file_reads_real_file():
    content = read_file("service/main.py")
    assert len(content) > 0


def test_read_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        read_file("service/nonexistent_file_xyz.py")


def test_read_file_escaping_root_raises():
    with pytest.raises(ValueError, match="escapes"):
        read_file("../../../../../etc/passwd")


# ── filesystem: write_file ────────────────────────────────────────────────────


def test_write_file_creates_file(tmp_path, monkeypatch):
    # Redirect PROJECT_ROOT so write is within "service/" relative to tmp_path
    fake_service = tmp_path / "service"
    fake_service.mkdir()
    import orchestrator.tools.filesystem as fs
    monkeypatch.setattr(fs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(fs, "SERVICE_DIR", fake_service)

    write_file("service/temp_test.py", "x = 1\n")
    assert (fake_service / "temp_test.py").read_text() == "x = 1\n"


def test_write_file_creates_parent_dirs(tmp_path, monkeypatch):
    fake_service = tmp_path / "service"
    fake_service.mkdir()
    import orchestrator.tools.filesystem as fs
    monkeypatch.setattr(fs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(fs, "SERVICE_DIR", fake_service)

    write_file("service/api/new_endpoint.py", "pass\n")
    assert (fake_service / "api" / "new_endpoint.py").exists()


def test_write_file_outside_service_raises(tmp_path, monkeypatch):
    import orchestrator.tools.filesystem as fs
    monkeypatch.setattr(fs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(fs, "SERVICE_DIR", tmp_path / "service")

    with pytest.raises(WriteGuardrailError, match="outside service/"):
        write_file("orchestrator/gateway/gateway.py", "malicious code")


def test_write_file_path_traversal_blocked(tmp_path, monkeypatch):
    fake_service = tmp_path / "service"
    fake_service.mkdir()
    import orchestrator.tools.filesystem as fs
    monkeypatch.setattr(fs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(fs, "SERVICE_DIR", fake_service)

    with pytest.raises(WriteGuardrailError):
        write_file("service/../../outside.py", "bad")


# ── filesystem: list_directory ────────────────────────────────────────────────


def test_list_directory_returns_entries():
    entries = list_directory("service")
    assert isinstance(entries, list)
    assert len(entries) > 0


def test_list_directory_sorted():
    entries = list_directory("service")
    assert entries == sorted(entries)


def test_list_directory_on_file_raises():
    with pytest.raises(NotADirectoryError):
        list_directory("service/main.py")


# ── filesystem: search_codebase ───────────────────────────────────────────────


def test_search_codebase_finds_known_string():
    results = search_codebase("FastAPI")
    assert len(results) > 0


def test_search_codebase_result_shape():
    results = search_codebase("FastAPI")
    for r in results:
        assert "file" in r
        assert "line" in r
        assert "content" in r
        assert isinstance(r["line"], int)


def test_search_codebase_no_match_returns_empty():
    results = search_codebase("ZZZNOMATCHSTRING_XYZ_12345")
    assert results == []


# ── test_runner: _parse_pytest_summary ───────────────────────────────────────


def test_parse_summary_all_passed():
    output = "12 passed in 0.45s"
    p, f, e = _parse_pytest_summary(output)
    assert p == 12 and f == 0 and e == 0


def test_parse_summary_mixed():
    output = "10 passed, 3 failed, 1 error in 1.2s"
    p, f, e = _parse_pytest_summary(output)
    assert p == 10 and f == 3 and e == 1


def test_parse_summary_empty_output():
    p, f, e = _parse_pytest_summary("")
    assert p == 0 and f == 0 and e == 0


# ── test_runner: run_tests ────────────────────────────────────────────────────


def test_run_tests_returns_expected_keys():
    result = run_tests("service/tests/")
    assert set(result.keys()) == {"passed", "failed", "errors", "output", "success"}


def test_run_tests_service_suite_passes():
    result = run_tests("service/tests/")
    assert result["success"] is True, (
        f"Service tests failed:\n{result['output']}"
    )


def test_run_tests_nonexistent_path_returns_errors():
    result = run_tests("service/tests/nonexistent_xyz/")
    # pytest collects nothing — passes 0 tests, output contains "no tests ran" or "collected 0"
    assert result["passed"] == 0


# ── test_runner: run_linter ───────────────────────────────────────────────────


def test_run_linter_returns_expected_keys():
    result = run_linter("service/")
    assert set(result.keys()) == {"passed", "violations", "count"}


def test_run_linter_violations_is_list():
    result = run_linter("service/")
    assert isinstance(result["violations"], list)


# ── github_client: mocked ────────────────────────────────────────────────────


def test_create_branch_calls_github_api():
    with patch("orchestrator.tools.github_client._repo") as mock_repo:
        repo = MagicMock()
        repo.full_name = "org/repo"
        source_branch = MagicMock()
        source_branch.commit.sha = "abc123"
        repo.get_branch.return_value = source_branch
        repo.create_git_ref.return_value = MagicMock()
        mock_repo.return_value = repo

        from orchestrator.tools.github_client import create_branch
        url = create_branch("feature/add-qr-endpoint")
        assert "feature/add-qr-endpoint" in url
        repo.create_git_ref.assert_called_once_with(
            ref="refs/heads/feature/add-qr-endpoint",
            sha="abc123",
        )


def test_create_pr_returns_number_and_url():
    with patch("orchestrator.tools.github_client._repo") as mock_repo:
        repo = MagicMock()
        pr = MagicMock()
        pr.number = 42
        pr.html_url = "https://github.com/org/repo/pull/42"
        repo.create_pull.return_value = pr
        mock_repo.return_value = repo

        from orchestrator.tools.github_client import create_pr
        number, url = create_pr("Add QR endpoint", "Description here", "feature/qr")
        assert number == 42
        assert "42" in url


def test_commit_and_push_runs_git_commands():
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = "abc1234"
    completed.stderr = ""

    with patch("orchestrator.tools.github_client.subprocess.run", return_value=completed) as mock_run:
        from orchestrator.tools.github_client import commit_and_push
        result = commit_and_push("feature/add-qr-endpoint", "feat: add QR code endpoint")

    assert "feature/add-qr-endpoint" in result
    commands = [call.args[0] for call in mock_run.call_args_list]
    assert any("checkout" in cmd for cmd in commands)
    assert any("add" in cmd for cmd in commands)
    assert any("commit" in cmd for cmd in commands)
    assert any("push" in cmd for cmd in commands)


def test_commit_and_push_raises_on_git_failure():
    failed = MagicMock()
    failed.returncode = 1
    failed.stdout = ""
    failed.stderr = "fatal: not a git repository"

    with patch("orchestrator.tools.github_client.subprocess.run", return_value=failed):
        from orchestrator.tools.github_client import commit_and_push
        import pytest
        with pytest.raises(RuntimeError, match="git command failed"):
            commit_and_push("feature/test-branch", "test commit")


def test_poll_pr_status_returns_merged_false_when_open():
    with patch("orchestrator.tools.github_client._repo") as mock_repo:
        repo = MagicMock()
        pr = MagicMock()
        pr.merged = False
        pr.merged_by = None
        pr.state = "open"
        repo.get_pull.return_value = pr
        mock_repo.return_value = repo

        from orchestrator.tools.github_client import poll_pr_status
        status = poll_pr_status(42)
        assert status["merged"] is False
        assert status["state"] == "open"
        assert status["merged_by"] is None


# ── registry: schema validation ───────────────────────────────────────────────


def test_registry_has_nine_schemas():
    assert len(TOOL_SCHEMAS) == 9


def test_all_schemas_have_required_keys():
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn


def test_schema_names_match_tool_keys():
    schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert schema_names == set(TOOLS.keys())


def test_registry_get_schemas_returns_list():
    registry = ToolRegistry()
    schemas = registry.get_schemas()
    assert isinstance(schemas, list)
    assert len(schemas) == 9


# ── registry: execute dispatch ────────────────────────────────────────────────


def test_registry_execute_read_file_real():
    registry = ToolRegistry()
    result = registry.execute("read_file", {"path": "service/main.py"})
    assert result.error is None
    assert len(result.result) > 0
    assert result.cache_hit is False


def test_registry_execute_unknown_tool_raises():
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="unknown tool"):
        registry.execute("nonexistent_tool", {})


def test_registry_execute_captures_tool_error():
    registry = ToolRegistry()
    result = registry.execute("read_file", {"path": "does_not_exist.py"})
    assert result.error is not None
    assert result.result is None


def test_registry_execute_measures_latency():
    registry = ToolRegistry()
    result = registry.execute("read_file", {"path": "service/main.py"})
    assert result.latency_ms >= 0


# ── registry: tool cache integration ─────────────────────────────────────────


def test_registry_caches_read_result():
    registry = ToolRegistry()
    cache = ToolCache()
    args = {"path": "service/main.py"}

    r1 = registry.execute("read_file", args, tool_cache=cache)
    r2 = registry.execute("read_file", args, tool_cache=cache)

    assert r1.cache_hit is False
    assert r2.cache_hit is True
    assert r1.result == r2.result


def test_registry_does_not_cache_write_file():
    registry = ToolRegistry()
    cache = ToolCache()
    # Even if we call write_file with a cache, result is NOT stored
    with patch("orchestrator.tools.filesystem.write_file", return_value=True):
        registry.execute("write_file", {"path": "service/x.py", "content": "x"}, tool_cache=cache)
    assert cache.size() == 0


def test_registry_does_not_cache_create_branch():
    registry = ToolRegistry()
    cache = ToolCache()
    with patch("orchestrator.tools.github_client.create_branch", return_value="http://url"):
        registry.execute("create_branch", {"branch_name": "feat/x"}, tool_cache=cache)
    assert cache.size() == 0


def test_registry_does_not_cache_commit_and_push():
    registry = ToolRegistry()
    cache = ToolCache()
    with patch("orchestrator.tools.github_client.commit_and_push", return_value="Committed and pushed"):
        registry.execute(
            "commit_and_push",
            {"branch_name": "feat/x", "commit_message": "feat: test"},
            tool_cache=cache,
        )
    assert cache.size() == 0

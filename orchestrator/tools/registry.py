"""
registry.py — ToolRegistry: the bridge between OpenAI function calling and Python.

How OpenAI function calling works
----------------------------------
When the stage agent calls the LLM (Phase 12), it passes TOOL_SCHEMAS as the
`tools=` argument.  OpenAI returns a message with `tool_calls` instead of
`content` when it decides to invoke a tool.  Each tool call contains:
  - id:       a unique call ID to echo back
  - function.name:      the tool name (must match a schema "name" field)
  - function.arguments: a JSON string of the arguments dict

The stage agent loop then calls ToolRegistry.execute(name, args) to run the
tool, appends the result as a "tool" role message, and re-calls the LLM.

TOOL_SCHEMAS
------------
The list of OpenAI-format tool schemas.  Passed verbatim as `tools=` in every
LLM call made by the stage agent.  Each schema follows the format:
  {
    "type": "function",
    "function": {
      "name":        str,
      "description": str,
      "parameters": {
        "type": "object",
        "properties": { param_name: {"type": ..., "description": ...}, ... },
        "required": [list of required param names]
      }
    }
  }

ToolCache integration
---------------------
execute() accepts an optional ToolCache instance (Phase 9).  When provided:
  1. Check cache — return cached result immediately on hit.
  2. On miss — run the tool, store result in cache, return result.
Tools that perform writes (write_file, create_branch, create_pr) skip caching
because their results represent side effects that must not be replayed.

Latency tracking
----------------
execute() measures wall-clock latency for every tool call and includes it in
the returned ToolResult.  MetricsTracker (Phase 16) will persist this to
orch_metrics.  For now it is available on the result for logging.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from orchestrator.cache.tool_cache import ToolCache
from orchestrator.tools import filesystem, github_client, test_runner

# Tools that mutate state — never cache their results
_NO_CACHE_TOOLS = frozenset({"write_file", "create_branch", "commit_and_push", "create_pr"})

# ── tool dispatch map ─────────────────────────────────────────────────────────

TOOLS: dict[str, Any] = {
    "read_file":        filesystem.read_file,
    "write_file":       filesystem.write_file,
    "list_directory":   filesystem.list_directory,
    "search_codebase":  filesystem.search_codebase,
    "run_tests":        test_runner.run_tests,
    "run_linter":       test_runner.run_linter,
    "create_branch":    github_client.create_branch,
    "commit_and_push":  github_client.commit_and_push,
    "create_pr":        github_client.create_pr,
    "poll_pr_status":   github_client.poll_pr_status,
}

# ── OpenAI function calling schemas ──────────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file in the project. "
                "Use this to inspect existing code before making changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root (e.g. 'service/api/endpoints.py')",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file inside the service/ directory or to requirements.txt. "
                "Creates the file (and any missing parent directories) if it does not exist. "
                "Use this to add new Python packages to requirements.txt when your implementation "
                "requires a library not already listed there."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root — must be under service/",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full UTF-8 file content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the entries (files and subdirectories) in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to project root",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": (
                "Search for a literal string across all Python files under service/. "
                "Returns file path, line number, and line content for each match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal string to search for",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run the pytest test suite against a path and return pass/fail counts. "
                "Always run this after writing or modifying code to verify correctness."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to test directory or file (default: 'service/tests/')",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_linter",
            "description": (
                "Run flake8 on the service/ directory and return any style violations. "
                "Run this after writing code to catch obvious style issues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to lint (default: 'service/')",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_branch",
            "description": "Create a new git branch from main on GitHub.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_name": {
                        "type": "string",
                        "description": "New branch name (e.g. 'feature/add-qr-endpoint')",
                    },
                    "from_branch": {
                        "type": "string",
                        "description": "Source branch to fork from (default: 'main')",
                    },
                },
                "required": ["branch_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_and_push",
            "description": (
                "Stage all changes under service/, create a git commit, and push "
                "to the feature branch. Call this after all write_file calls are "
                "done and before create_pr. Without this step the branch has no "
                "commits and create_pr will fail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_name": {
                        "type": "string",
                        "description": "Feature branch to push to (must already exist via create_branch)",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message (e.g. 'feat: add QR code endpoint')",
                    },
                },
                "required": ["branch_name", "commit_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pr",
            "description": (
                "Open a GitHub pull request and return the PR number and URL. "
                "Call this after all code has been written and tests pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "PR title"},
                    "body":  {"type": "string", "description": "PR description in markdown"},
                    "branch": {"type": "string", "description": "Feature branch to merge from"},
                    "base":   {
                        "type": "string",
                        "description": "Target branch to merge into (default: 'main')",
                    },
                },
                "required": ["title", "body", "branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poll_pr_status",
            "description": "Check whether a pull request has been merged or closed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pr_number": {
                        "type": "integer",
                        "description": "The PR number returned by create_pr",
                    }
                },
                "required": ["pr_number"],
            },
        },
    },
]


# ── result dataclass ──────────────────────────────────────────────────────────


@dataclass
class ToolResult:
    tool_name: str
    args: dict
    result: Any
    latency_ms: float
    cache_hit: bool = False
    error: str | None = None


# ── registry ──────────────────────────────────────────────────────────────────


class ToolRegistry:
    """Dispatches tool calls from the LLM to the corresponding Python functions.

    Usage in StageAgent (Phase 12):
        registry = ToolRegistry()
        tool_cache = ToolCache(state.get("tool_cache", {}))
        for tool_call in message.tool_calls:
            name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])
            result = registry.execute(name, args, tool_cache=tool_cache)
    """

    def get_schemas(self) -> list[dict]:
        """Return the OpenAI tool schemas list for the `tools=` parameter."""
        return TOOL_SCHEMAS

    def execute(
        self,
        tool_name: str,
        args: dict,
        tool_cache: ToolCache | None = None,
    ) -> ToolResult:
        """Execute a tool by name, with optional ToolCache.

        Args:
            tool_name:  Must be a key in TOOLS.
            args:       Arguments dict — passed as **kwargs to the tool function.
            tool_cache: Optional ToolCache for read-only tools.  Write tools
                        (write_file, create_branch, create_pr) always bypass cache.

        Returns:
            ToolResult with result, latency_ms, and cache_hit flag.

        Raises:
            KeyError: if tool_name is not registered.
            Any exception raised by the tool function.
        """
        if tool_name not in TOOLS:
            raise KeyError(
                f"ToolRegistry: unknown tool '{tool_name}'. "
                f"Registered tools: {sorted(TOOLS.keys())}"
            )

        cacheable = tool_cache is not None and tool_name not in _NO_CACHE_TOOLS

        if cacheable:
            cached = tool_cache.get(tool_name, args)
            if cached is not None:
                return ToolResult(
                    tool_name=tool_name,
                    args=args,
                    result=cached,
                    latency_ms=0.0,
                    cache_hit=True,
                )

        start = time.monotonic()
        try:
            result = TOOLS[tool_name](**args)
            error = None
        except Exception as exc:
            result = None
            error = str(exc)
        latency_ms = (time.monotonic() - start) * 1000

        if cacheable and error is None:
            tool_cache.set(tool_name, args, result)

        return ToolResult(
            tool_name=tool_name,
            args=args,
            result=result,
            latency_ms=latency_ms,
            cache_hit=False,
            error=error,
        )

"""
orchestrator.tools — Tool registry for the stage agent's function calling.

Overview
--------
The stage agent (gpt-4o) can call tools during its execution loop.  Every tool
call flows through ToolRegistry, which adds:
  - Tool result cache lookup (read-through, keyed on tool name + args hash)
  - Tool latency measurement (written to orch_metrics.tool_latency_ms)
  - Safety enforcement (write_file path must be under service/)

Available tools
---------------
read_file(path)                 Read a file relative to the project root.
write_file(path, content)       Write to a file under service/ only.
                                Raises GuardrailError if path is outside service/.
search_codebase(query)          grep -rn across service/ *.py files.
                                Returns list of {file, line, content} matches.
run_tests(path?)                Run pytest, return {passed, failed, output}.
run_linter()                    Run flake8/ruff, return {passed, violations}.
create_branch(name)             Create a git branch from main via GitHub API.
create_pr(title, body, branch)  Open a GitHub PR, return (pr_number, pr_url).
poll_pr_status(pr_number)       Check if PR is merged/closed via GitHub API.

Write safety constraint
-----------------------
write_file enforces that the resolved path is under the project's service/
directory.  This is the key guardrail that prevents the agent from modifying
orchestrator code, config files, or anything outside the intended scope.
Violation raises GuardrailError before any filesystem write occurs.

Why OpenAI function calling instead of LangChain/LlamaIndex tools?
-------------------------------------------------------------------
OpenAI's native function calling is deterministic, well-typed, and directly
supported by the models we use.  Third-party agent frameworks add abstraction
and dependency weight without providing capabilities we need.  Keeping tool
definitions as plain Python dicts (TOOL_SCHEMAS in registry.py) means every
tool's interface is visible and auditable in one file.

Implemented in Phase 10.
"""

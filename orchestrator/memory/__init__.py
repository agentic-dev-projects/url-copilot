"""
orchestrator.memory — Cross-run persistent memory store.

Overview
--------
Memory allows the orchestrator to accumulate knowledge across runs and inject
it into every stage prompt as context.  Think of it as a team knowledge base
that grows over time: each tech lead approval comment, each architectural
decision, each assumption confirmed during a clarification loop is saved and
reused in future runs.

Memory types
------------
fact          Immutable codebase truths (seeded from seeds.yaml on first run).
              Example: "Test suite uses SQLite in-memory."
preference    Reviewer preferences captured from approval comments.
              Example: "prefer segno over qrcode for QR generation (bob)"
decision      Architectural choices made during a run.
              Example: "Cache key format: url_cache:{short_code}, TTL=3600s"
convention    Coding patterns the team enforces.
              Example: "Cache invalidation required in url_service on update+delete"

Files
-----
store.py    MemoryStore — read/write orch_memory table.
            seed_if_empty() loads seeds.yaml on first run.
            format_for_prompt() returns a formatted block for Prompt Builder Layer 3.

seeds.yaml  5 hardcoded codebase facts loaded on first run if orch_memory is empty.

How memory flows into prompts
------------------------------
MemoryStore.format_for_prompt() is called by PromptBuilder for every stage.
The output is injected as Layer 3 of the 7-layer prompt assembly.  This means
every stage agent automatically knows team conventions and reviewer preferences
without those facts being repeated in every stage-specific prompt.

Implemented in Phase 8.
"""

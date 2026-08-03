"""
orchestrator.prompt_builder — 7-layer prompt assembly.

Overview
--------
The PromptBuilder assembles the final list of OpenAI messages for each stage
call.  It composes 7 layers in a specific order designed to maximize OpenAI's
server-side prompt caching (static content first, dynamic content last).

Layer assembly order
--------------------
1. Stage system prompt     STATIC — loaded from prompts/{stage}_v{n}.txt
                           Same for every call to this stage → cache prefix.
2. Codebase context        STATIC per run — key service files read at run start.
                           Same across all calls in one run → cache prefix.
3. Long-term memory        From MemoryStore.format_for_prompt() — team facts,
                           reviewer preferences, architectural decisions.
4. Cross-stage context     From RunContext.stage_artifacts (prior stage outputs)
                           + RunContext.stage_evaluations (HybridFeedback from
                           the evaluator — AI scores + human comments).
5. Conversation history    Prior turns within the current stage (tool calls).
6. Tool results            Accumulated results from tool calls this turn.
7. Stage instruction       The specific directive for this stage execution.
                           Dynamic — not cached.

Why static layers first?
------------------------
OpenAI's prompt cache works on the common prefix of the messages list.
If Layers 1+2 are identical across many calls (which they are within a run),
OpenAI reuses the KV cache for those tokens — reducing both latency and cost
by roughly 50% on multi-call stages.  Placing dynamic content at the end
ensures the cacheable prefix is as long as possible.

Prompt versioning
-----------------
Each prompt file is named {stage}_v{n}.txt.  When a prompt is updated,
the version number increments and the old file is kept for comparison.
PromptLoader returns both the prompt text and the version string
(e.g., "architecture_v1"), which is recorded in orch_stage_results.prompt_version
for full reproducibility and cost attribution.

Files
-----
loader.py   PromptLoader — reads versioned prompt files from orchestrator/prompts/.
builder.py  PromptBuilder — assembles the 7 layers into an OpenAI messages list.

Implemented in Phase 11.
"""

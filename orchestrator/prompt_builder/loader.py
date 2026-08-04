"""
PromptLoader — loads versioned stage prompt files from orchestrator/prompts/stages/.

Version resolution
------------------
Prompt files are named {stage_name}_v{N}.txt.  PromptLoader always loads the
highest-numbered version available for a given stage name (latest-wins strategy).
This lets us iterate on prompts (architecture_v2.txt, architecture_v3.txt) without
changing call sites — callers just call load("architecture_design") and always get
the current best version.

The version string embedded in the first line (# version: {stage_name}_v{N}) is
returned alongside the text and recorded in StageResult.prompt_version for
reproducibility and cost attribution.

PROMPTS_DIR
-----------
Resolved relative to this file so it works regardless of the process working
directory (important for pytest which is run from the project root, not from
orchestrator/).
"""

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "stages"


class PromptLoader:
    """Loads the latest versioned prompt file for a given stage name."""

    def load(self, stage_name: str) -> tuple[str, str]:
        """Return (prompt_text, version_string) for the latest version of stage_name.

        Args:
            stage_name: e.g. "architecture_design", "classifier"

        Returns:
            (prompt_text, version_string) where version_string is e.g.
            "architecture_design_v1".

        Raises:
            FileNotFoundError: if no prompt file exists for this stage.
        """
        path, version = self._resolve(stage_name)
        text = path.read_text(encoding="utf-8")
        return text, version

    # ── private ───────────────────────────────────────────────────────────────

    def _resolve(self, stage_name: str) -> tuple[Path, str]:
        """Find the highest-versioned prompt file for stage_name."""
        pattern = re.compile(rf"^{re.escape(stage_name)}_v(\d+)\.txt$")
        candidates: list[tuple[int, Path]] = []
        if PROMPTS_DIR.is_dir():
            for p in PROMPTS_DIR.iterdir():
                m = pattern.match(p.name)
                if m:
                    candidates.append((int(m.group(1)), p))
        if not candidates:
            raise FileNotFoundError(
                f"PromptLoader: no prompt file found for stage '{stage_name}' "
                f"in {PROMPTS_DIR}. Expected a file matching '{stage_name}_v*.txt'."
            )
        _, path = max(candidates, key=lambda t: t[0])
        version = path.stem   # e.g. "architecture_design_v1"
        return path, version

from __future__ import annotations

import re
from pathlib import Path

from .config import ProjectConfig, resolve_stage
from .display import console, show_stage_header, show_error
from .exceptions import PromptError, StageError
from .state import ProjectState, save_state

# Ordered list of stage names as they appear in the pipeline
STAGE_ORDER = [
    "stage0_seed",
    "stage1_bible",
    "stage2_world",
    "stage3_characters",
    "stage4_outlines",
    "stage5_chapters",
    "stage6_batch_review",
    "stage7_export",
]

STAGE_LABELS = {
    "stage0_seed": (0, "Seed Analysis"),
    "stage1_bible": (1, "Story Bible"),
    "stage2_world": (2, "World Builder"),
    "stage3_characters": (3, "Characters"),
    "stage4_outlines": (4, "Chapter Outlines"),
    "stage5_chapters": (5, "Chapter Writing"),
    "stage6_batch_review": (6, "Batch Review"),
    "stage7_export": (7, "Export"),
}


def _load_prompt(prompts_dir: Path, filename: str) -> str:
    path = prompts_dir / filename
    if not path.exists():
        raise PromptError(
            f"Prompt file not found: {path}\nExpected at: {path.resolve()}"
        )
    return path.read_text(encoding="utf-8")


def _fill(template: str, **kwargs: str) -> str:
    """Replace [[key]] placeholders with values."""
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"[[{key}]]", value or "")
    return result


class Pipeline:
    def __init__(self, cfg: ProjectConfig, project_dir: Path):
        self.cfg = cfg
        self.project_dir = project_dir
        self.prompts_dir = cfg.prompts_dir

    _ISSUES_FILE = "outstanding_issues.md"
    # Stages that should NOT receive the outstanding issues injection
    _NO_ISSUES_STAGES = {"seed_analysis", "export"}

    def build_system_prompt(self, stage_name: str) -> str:
        """Build the system prompt: persona template + outstanding issues (where relevant)."""
        persona_template = _load_prompt(self.prompts_dir, "system_persona.txt")
        system = _fill(
            persona_template,
            persona_name=self.cfg.persona.name,
            persona_description=self.cfg.persona.description,
        )

        if stage_name not in self._NO_ISSUES_STAGES:
            issues_path = self.project_dir / self._ISSUES_FILE
            if issues_path.exists():
                issues_text = issues_path.read_text(encoding="utf-8").strip()
                if issues_text:
                    system += (
                        "\n\n---\n"
                        "OUTSTANDING ISSUES FROM SEED ANALYSIS\n"
                        "These issues were raised during the initial story analysis and not yet fully "
                        "resolved. Watch for opportunities to address them naturally in your output. "
                        "Do not force them — but when they are relevant, engage with them directly.\n\n"
                        + issues_text
                    )

        return system

    def build_user_prompt(self, filename: str, **kwargs: str) -> str:
        template = _load_prompt(self.prompts_dir, filename)
        return _fill(template, **kwargs)

    def read_file(self, *parts: str) -> str:
        """Read a project file, returning empty string if it doesn't exist."""
        path = self.project_dir.joinpath(*parts)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_file(self, content: str, *parts: str) -> Path:
        """Write content to a project file, creating parent dirs as needed."""
        path = self.project_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run(self, state: ProjectState) -> None:
        """Run the pipeline from the current stage forward."""
        start_idx = (
            STAGE_ORDER.index(state.current_stage)
            if state.current_stage in STAGE_ORDER
            else 0
        )

        for stage_name in STAGE_ORDER[start_idx:]:
            num, label = STAGE_LABELS[stage_name]
            show_stage_header(label, num)

            try:
                state = self._run_stage(stage_name, state)
            except StageError as e:
                show_error(str(e))
                break
            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted — progress saved.[/dim]")
                save_state(state, self.project_dir)
                break

    def _run_stage(self, stage_name: str, state: ProjectState) -> ProjectState:
        # Import stage module on demand to avoid circular imports
        if stage_name == "stage0_seed":
            from .stages.stage0_seed import run
        elif stage_name == "stage1_bible":
            from .stages.stage1_bible import run
        elif stage_name == "stage2_world":
            from .stages.stage2_world import run
        elif stage_name == "stage3_characters":
            from .stages.stage3_characters import run
        elif stage_name == "stage4_outlines":
            from .stages.stage4_outlines import run
        elif stage_name == "stage5_chapters":
            from .stages.stage5_chapters import run
        elif stage_name == "stage6_batch_review":
            from .stages.stage6_batch_review import run
        elif stage_name == "stage7_export":
            from .stages.stage7_export import run
        else:
            raise StageError(f"Unknown stage: {stage_name}")

        return run(self, state)

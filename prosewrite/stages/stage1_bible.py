from __future__ import annotations

import re

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_draft, show_info, show_success, show_warning, word_count
from ..exceptions import StageError
from ..state import ProjectState, save_state

_CHAPTER_COUNT_RE = re.compile(r"CHAPTER\s+COUNT:\s*(\d+)", re.IGNORECASE)


def _extract_chapter_count(text: str) -> int | None:
    match = _CHAPTER_COUNT_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 1 — Story Bible."""
    seed_text = pipeline.read_file("seed.md")
    seed_analysis = pipeline.read_file("seed_analysis.md")
    if not seed_text.strip():
        raise StageError("seed.md is missing.")

    stage_cfg = resolve_stage(pipeline.cfg, "story_bible")
    system = pipeline.build_system_prompt("story_bible")
    loop = ApprovalLoop()

    while True:
        show_info(f"Generating story bible with {stage_cfg.model} (temp={stage_cfg.temperature})…")
        user_prompt = pipeline.build_user_prompt(
            "stage1.txt",
            project_name=state.project_name,
            seed_content=seed_text,
            seed_analysis_content=seed_analysis,
        )
        with LLMClient(stage_cfg) as client:
            bible = client.complete(system, [{"role": "user", "content": user_prompt}])

        chapter_count = _extract_chapter_count(bible)
        if chapter_count is None:
            show_warning(
                "Could not extract chapter count from story bible output.\n"
                "The model should include a line like 'CHAPTER COUNT: 24'.\n"
                "You will be prompted to confirm the chapter count after approval."
            )

        show_draft(bible, title="Story Bible", word_count=word_count(bible))

        action, user_text = loop.wait("Approve story bible, request changes, or type 'redo'")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(bible, "story_bible.md")
            show_success("story_bible.md saved.")

            # Resolve chapter count
            if chapter_count is None:
                from rich.prompt import IntPrompt
                chapter_count = IntPrompt.ask("How many chapters does this novel have?")

            state.settings.total_chapters = chapter_count
            state.current_stage = "stage2_world"
            save_state(state, pipeline.project_dir)
            show_success(f"Chapter count set to {chapter_count}.")
            return state

        elif action == ApprovalAction.REGENERATE:
            console.print("[dim]Regenerating…[/dim]")
            continue

        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            with LLMClient(stage_cfg) as client:
                bible = client.complete(system, [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": bible},
                    {"role": "user", "content": user_text},
                ])
            continue

        elif action == ApprovalAction.EDIT:
            bible = user_text
            chapter_count = _extract_chapter_count(bible)
            if chapter_count is None:
                from rich.prompt import IntPrompt
                chapter_count = IntPrompt.ask("How many chapters does this novel have?")
            pipeline.write_file(bible, "story_bible.md")
            state.settings.total_chapters = chapter_count
            state.current_stage = "stage2_world"
            save_state(state, pipeline.project_dir)
            show_success("story_bible.md saved (manual edit).")
            return state

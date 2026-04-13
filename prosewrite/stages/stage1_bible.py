from __future__ import annotations

import re

import questionary

from ..approval import ApprovalAction, ApprovalLoop, _STYLE
from ..client import LLMClient
from ..config import resolve_stage
from ..display import show_info, show_success, show_warning, stream_response, word_count
from ..exceptions import StageError
from ..state import ProjectState, save_state

_CHAPTER_COUNT_RE = re.compile(r"CHAPTER\s+COUNT:\s*(\d+)", re.IGNORECASE)


def _extract_chapter_count(text: str) -> int | None:
    match = _CHAPTER_COUNT_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def _build_prompt(pipeline, state: ProjectState, seed_text: str, seed_analysis: str, brief: str) -> str:
    prompt = pipeline.build_user_prompt(
        "stage1.txt",
        project_name=state.project_name,
        seed_content=seed_text,
        seed_analysis_content=seed_analysis,
    )
    if brief:
        prompt += (
            f"\n\n⚠ AUTHOR GUIDANCE — PRIORITY INSTRUCTION:\n{brief}\n"
            f"You MUST incorporate this guidance. It overrides default section structure where needed."
        )
    return prompt


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 1 — Story Bible."""
    seed_text = pipeline.read_file("seed.md")
    seed_analysis = pipeline.read_file("seed_analysis.md")
    if not seed_text.strip():
        raise StageError("seed.md is missing.")

    stage_cfg = resolve_stage(pipeline.cfg, "story_bible")
    system = pipeline.build_system_prompt("story_bible")
    loop = ApprovalLoop()

    messages: list[dict] = []
    brief = ""

    while True:
        if brief:
            show_info(f"Regenerating with guidance: '{brief}'…")
        else:
            show_info(f"Generating story bible with {stage_cfg.model} (temp={stage_cfg.temperature})…")

        user_prompt = _build_prompt(pipeline, state, seed_text, seed_analysis, brief)
        brief = ""  # consume the brief

        messages = [{"role": "user", "content": user_prompt}]
        with LLMClient(stage_cfg) as client:
            bible = stream_response(client.stream(system, messages), title="Story Bible")
        messages.append({"role": "assistant", "content": bible})

        chapter_count = _extract_chapter_count(bible)
        if chapter_count is None:
            show_warning(
                "Could not extract chapter count from story bible output.\n"
                "The model should include a line like 'CHAPTER COUNT: 24'.\n"
                "You will be prompted to confirm the chapter count after approval."
            )

        action, user_text = loop.wait("Story Bible")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(bible, "story_bible.md")
            show_success("story_bible.md saved.")

            if chapter_count is None:
                raw = questionary.text("How many chapters does this novel have?", style=_STYLE).ask() or "0"
                chapter_count = int(raw.strip()) if raw.strip().isdigit() else 0

            state.settings.total_chapters = chapter_count
            state.current_stage = "stage2_world"
            save_state(state, pipeline.project_dir)
            show_success(f"Chapter count set to {chapter_count}.")
            return state

        elif action == ApprovalAction.REGENERATE:
            brief = user_text  # may be empty (bare redo) or a guidance note

        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            messages.append({"role": "user", "content": user_text})
            with LLMClient(stage_cfg) as client:
                bible = stream_response(
                    client.stream(system, messages), title="Story Bible", border_style="cyan"
                )
            messages.append({"role": "assistant", "content": bible})

        elif action == ApprovalAction.EDIT:
            bible = user_text
            chapter_count = _extract_chapter_count(bible)
            if chapter_count is None:
                raw = questionary.text("How many chapters does this novel have?", style=_STYLE).ask() or "0"
                chapter_count = int(raw.strip()) if raw.strip().isdigit() else 0
            pipeline.write_file(bible, "story_bible.md")
            state.settings.total_chapters = chapter_count
            state.current_stage = "stage2_world"
            save_state(state, pipeline.project_dir)
            show_success("story_bible.md saved (manual edit).")
            return state

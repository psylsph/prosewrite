from __future__ import annotations

import re

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_draft, show_info, show_review, show_success, show_warning, word_count
from ..exceptions import StageError
from ..reviewer import AIReviewer
from ..state import ProjectState, save_state

_LOW_SCORE_THRESHOLD = 7.0


def _parse_chapter_list(chapter_list_text: str) -> list[tuple[int, str]]:
    """
    Parse the chapter list table. Returns list of (chapter_num, title).
    Expects a Markdown table with # as first column, Title as second.
    """
    chapters: list[tuple[int, str]] = []
    header_seen = False
    separator_seen = False

    for line in chapter_list_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[-| :]+\|$", stripped):
            separator_seen = True
            continue
        if not separator_seen:
            header_seen = True
            continue
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) >= 2:
            try:
                num = int(re.sub(r"\D", "", parts[0]))
                title = parts[1].strip()
                if num > 0:
                    chapters.append((num, title))
            except (ValueError, IndexError):
                continue

    return chapters


def _get_chapter_list_entry(chapter_list_text: str, chapter_num: int) -> str:
    for line in chapter_list_text.splitlines():
        if line.strip().startswith("|"):
            parts = [p.strip() for p in line.strip("|").split("|")]
            if parts and re.sub(r"\D", "", parts[0]) == str(chapter_num):
                return line.strip()
    return f"| {chapter_num} | Chapter {chapter_num} | ... |"


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 4 — Chapter Outlines."""
    story_bible = pipeline.read_file("story_bible.md")
    world_text = pipeline.read_file("world.md")
    character_index = pipeline.read_file("character_index.md")

    if not story_bible.strip():
        raise StageError("story_bible.md is missing. Run Stage 1 first.")

    total_chapters = state.settings.total_chapters
    if total_chapters == 0:
        from rich.prompt import IntPrompt
        total_chapters = IntPrompt.ask("How many chapters does this novel have?")
        state.settings.total_chapters = total_chapters

    stage_cfg = resolve_stage(pipeline.cfg, "chapter_outlines")
    system = pipeline.build_system_prompt("chapter_outlines")
    reviewer = AIReviewer(pipeline)

    # -----------------------------------------------------------------------
    # Step 4a — Chapter List
    # -----------------------------------------------------------------------
    console.print("\n[bold]Step 4a — Chapter List[/bold]")

    list_task = pipeline.build_user_prompt(
        "stage4_list_task.txt",
        total_chapters=str(total_chapters),
    )
    list_prompt = pipeline.build_user_prompt(
        "stage4.txt",
        project_name=state.project_name,
        story_bible_content=story_bible,
        world_content=world_text,
        character_index_content=character_index,
        task=list_task,
    )

    loop = ApprovalLoop()
    chapter_list_text = ""
    chapters: list[tuple[int, str]] = []

    while True:
        show_info(f"Generating chapter list with {stage_cfg.model}…")
        with LLMClient(stage_cfg) as client:
            chapter_list_text = client.complete(system, [{"role": "user", "content": list_prompt}])

        chapters = _parse_chapter_list(chapter_list_text)
        show_draft(chapter_list_text, title=f"Chapter List ({len(chapters)} chapters)")

        if len(chapters) != total_chapters:
            show_warning(
                f"Expected {total_chapters} chapters but parsed {len(chapters)}. "
                "Check the table format before approving."
            )

        action, user_text = loop.wait("Approve chapter list, request changes, or 'redo'")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(chapter_list_text, "chapter_outlines/chapter_list.md")
            show_success("chapter_list.md saved.")
            chapters = _parse_chapter_list(chapter_list_text) or [(i, f"Chapter {i}") for i in range(1, total_chapters + 1)]
            break
        elif action == ApprovalAction.REGENERATE:
            continue
        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            with LLMClient(stage_cfg) as client:
                chapter_list_text = client.complete(system, [
                    {"role": "user", "content": list_prompt},
                    {"role": "assistant", "content": chapter_list_text},
                    {"role": "user", "content": user_text},
                ])
            continue
        elif action == ApprovalAction.EDIT:
            chapter_list_text = user_text
            pipeline.write_file(chapter_list_text, "chapter_outlines/chapter_list.md")
            chapters = _parse_chapter_list(chapter_list_text) or [(i, f"Chapter {i}") for i in range(1, total_chapters + 1)]
            show_success("chapter_list.md saved (manual edit).")
            break

    # -----------------------------------------------------------------------
    # Step 4b — Per-chapter outlines
    # -----------------------------------------------------------------------
    console.print(f"\n[bold]Step 4b — Chapter Outlines ({len(chapters)} chapters)[/bold]")

    already_approved = set(state.progress.approved_outlines)
    outline_loop = ApprovalLoop(allow_skip=True)

    macro_summary = pipeline.read_file("summaries/macro.md")

    for chapter_num, chapter_title in chapters:
        if chapter_num in already_approved:
            show_info(f"Chapter {chapter_num} outline already approved — skipping.")
            continue

        console.print(f"\n[bold cyan]Chapter {chapter_num}: {chapter_title}[/bold cyan]")

        list_entry = _get_chapter_list_entry(chapter_list_text, chapter_num)
        outline_task = pipeline.build_user_prompt(
            "stage4_outline_task.txt",
            chapter_num=str(chapter_num),
            chapter_title=chapter_title,
            chapter_list_entry=list_entry,
            macro_summary=macro_summary or "(no summary yet — this is an early chapter)",
            character_profiles="(see character profiles in characters/ directory)",
        )
        outline_prompt = pipeline.build_user_prompt(
            "stage4.txt",
            project_name=state.project_name,
            story_bible_content=story_bible,
            world_content=world_text,
            character_index_content=character_index,
            task=outline_task,
        )

        while True:
            show_info(f"Generating outline for Chapter {chapter_num}…")
            with LLMClient(stage_cfg) as client:
                outline = client.complete(system, [{"role": "user", "content": outline_prompt}])

            # Inline AI review
            show_info("Running outline review…")
            review = reviewer.review_outline(chapter_num, outline, story_bible, character_index)
            show_review(review)

            if review.score < _LOW_SCORE_THRESHOLD:
                show_warning(f"Review score {review.score}/10 is below threshold. Consider requesting changes.")

            show_draft(outline, title=f"Chapter {chapter_num} Outline — {chapter_title}", word_count=word_count(outline))

            action, user_text = outline_loop.wait(
                f"Approve outline, request changes, 'redo', 'use review', or 'skip'"
            )

            outline_path = f"chapter_outlines/chapter_{chapter_num}_outline.md"

            if action == ApprovalAction.APPROVE:
                pipeline.write_file(outline, outline_path)
                state.progress.approved_outlines.append(chapter_num)
                save_state(state, pipeline.project_dir)
                show_success(f"{outline_path} saved.")
                break
            elif action == ApprovalAction.SKIP:
                show_info(f"Chapter {chapter_num} outline skipped.")
                break
            elif action == ApprovalAction.REGENERATE:
                continue
            elif action == ApprovalAction.USE_REVIEW:
                show_info("Regenerating using review as brief…")
                with LLMClient(stage_cfg) as client:
                    outline = client.complete(system, [
                        {"role": "user", "content": outline_prompt},
                        {"role": "assistant", "content": outline},
                        {"role": "user", "content": f"Please revise based on this review:\n{review.full_text}"},
                    ])
                continue
            elif action == ApprovalAction.FEEDBACK:
                show_info("Incorporating feedback…")
                with LLMClient(stage_cfg) as client:
                    outline = client.complete(system, [
                        {"role": "user", "content": outline_prompt},
                        {"role": "assistant", "content": outline},
                        {"role": "user", "content": user_text},
                    ])
                continue
            elif action == ApprovalAction.EDIT:
                outline = user_text
                pipeline.write_file(outline, outline_path)
                state.progress.approved_outlines.append(chapter_num)
                save_state(state, pipeline.project_dir)
                show_success(f"{outline_path} saved (manual edit).")
                break

    state.current_stage = "stage5_chapters"
    save_state(state, pipeline.project_dir)
    return state

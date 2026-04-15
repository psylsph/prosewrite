from __future__ import annotations

import re

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import (
    ReviewResult,
    console,
    show_info,
    show_review,
    show_success,
    show_warning,
    stream_response,
)
from ..exceptions import StageError
from ..reviewer import AIReviewer
from ..state import ProjectState, save_state

_LOW_SCORE_THRESHOLD = 7.0
_AUTO_APPROVE_SCORE_THRESHOLD = 9.0


def _gather_previous_outlines(
    pipeline,
    chapter_num: int,
    chapters: list[tuple[int, str]],
) -> str:
    """Gather previous chapter outlines for continuity checking."""
    previous_outlines = ""
    chapters_to_include = 3  # Include up to 3 previous outlines

    # Find the index of current chapter in the chapters list
    current_idx = next(
        (i for i, (num, _) in enumerate(chapters) if num == chapter_num), 0
    )

    # Include previous chapters' outlines
    start_idx = max(0, current_idx - chapters_to_include)
    for i in range(start_idx, current_idx):
        prev_num, prev_title = chapters[i]
        outline_path = f"chapter_outlines/chapter_{prev_num}_outline.md"
        if (pipeline.project_dir / outline_path).exists():
            outline_text = pipeline.read_file(outline_path)
            previous_outlines += (
                f"\n\n## Chapter {prev_num}: {prev_title}\n\n{outline_text}\n\n---"
            )

    return previous_outlines


def _run_final_outline_review(
    reviewer,
    chapter_num: int,
    outline_text: str,
    story_bible: str,
    character_index: str,
    previous_outlines: str = "",
) -> "ReviewResult":
    """Run a final AI review on a revised outline."""
    from ..display import show_info

    show_info("Running final outline review…")
    final_review = reviewer.review_outline(
        chapter_num=chapter_num,
        outline_text=outline_text,
        story_bible=story_bible,
        character_index=character_index,
        previous_outlines=previous_outlines,
    )
    return final_review


_HOOK_HEADER = re.compile(
    r"^\*\*Chapter\s+(\d+)\s*[—–-]\s*(.+?)\*\*\s*$", re.IGNORECASE
)


def _parse_chapter_list(chapter_list_text: str) -> list[tuple[int, str]]:
    """
    Parse chapter numbers and titles from **Chapter N — Title** hook headers.
    Returns list of (chapter_num, title) in document order.
    """
    chapters: list[tuple[int, str]] = []
    for line in chapter_list_text.splitlines():
        m = _HOOK_HEADER.match(line.strip())
        if m:
            chapters.append((int(m.group(1)), m.group(2).strip()))
    return chapters


def _get_chapter_list_entry(chapter_list_text: str, chapter_num: int) -> str:
    """Return the hook header line for a given chapter number."""
    for line in chapter_list_text.splitlines():
        m = _HOOK_HEADER.match(line.strip())
        if m and int(m.group(1)) == chapter_num:
            return line.strip()
    return f"**Chapter {chapter_num}**"


def _get_chapter_hook(chapter_list_text: str, chapter_num: int) -> str:
    """
    Extract the full hook block for a given chapter from the chapter list.
    Returns the block text, or a fallback if not found.
    """
    lines = chapter_list_text.splitlines()
    target = re.compile(
        rf"^\*\*Chapter\s+{re.escape(str(chapter_num))}\b", re.IGNORECASE
    )

    start = None
    for i, line in enumerate(lines):
        if target.match(line.strip()):
            start = i
            break

    if start is None:
        return f"(No hook entry found for Chapter {chapter_num} — infer from context.)"

    block: list[str] = []
    for line in lines[start:]:
        if block and (_HOOK_HEADER.match(line.strip()) or line.strip() == "---"):
            break
        block.append(line)

    return "\n".join(block).strip()


def _with_brief(prompt: str, brief: str, context: str = "output") -> str:
    if brief:
        return (
            prompt + f"\n\n⚠ AUTHOR GUIDANCE — PRIORITY INSTRUCTION:\n{brief}\n"
            f"You MUST incorporate this guidance. It overrides default section structure where needed."
        )
    return prompt


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 4 — Chapter Outlines."""
    seed_text = pipeline.read_file("seed.md")
    story_bible = pipeline.read_file("story_bible.md")
    world_text = pipeline.read_file("world.md")
    character_index = pipeline.read_file("character_index.md")

    if not story_bible.strip():
        raise StageError("story_bible.md is missing. Run Stage 1 first.")
    if not world_text.strip():
        raise StageError("world.md is empty. Run Stage 2 first.")
    if not character_index.strip():
        raise StageError("character_index.md is empty. Run Stage 3 first.")

    # total_chapters may be 0 or a rough estimate — the actual count will be determined
    # by the chapter list generation based on story scope
    total_chapters = state.settings.total_chapters

    stage_cfg = resolve_stage(pipeline.cfg, "chapter_outlines")
    system = pipeline.build_system_prompt("chapter_outlines")
    reviewer = AIReviewer(pipeline)

    # -----------------------------------------------------------------------
    # Step 4a — Chapter List
    # -----------------------------------------------------------------------
    console.print("\n[bold]Step 4a — Chapter List[/bold]")

    list_task = pipeline.build_user_prompt(
        "stage4_list_task.txt",
    )
    base_list_prompt = pipeline.build_user_prompt(
        "stage4.txt",
        project_name=state.project_name,
        seed_content=seed_text,
        story_bible_content=story_bible,
        world_content=world_text,
        character_index_content=character_index,
        task=list_task,
    )

    loop = ApprovalLoop(allow_use_review=True)
    chapter_list_text = ""
    chapters: list[tuple[int, str]] = []
    messages: list[dict] = []
    brief = ""
    need_generation = True

    # Resume: load existing chapter list from disk if present
    existing_list = pipeline.read_file("chapter_outlines/chapter_list.md")
    if existing_list.strip():
        chapter_list_text = existing_list
        chapters = _parse_chapter_list(chapter_list_text)
        show_info(f"Loaded existing chapter_list.md ({len(chapters)} chapters found).")
        stream_response(iter([chapter_list_text]), title="Chapter List (existing)")
        action, user_text = loop.wait("Chapter List (existing)")
        if action == ApprovalAction.APPROVE:
            # Go to review section
            need_generation = False
        elif action == ApprovalAction.EDIT:
            chapter_list_text = user_text
            pipeline.write_file(chapter_list_text, "chapter_outlines/chapter_list.md")
            chapters = _parse_chapter_list(chapter_list_text)
            if not chapters:
                # Fallback if parsing fails
                chapters = [(i, f"Chapter {i}") for i in range(1, total_chapters + 1)]
            # Update chapter count based on actual chapters
            actual_chapters = len(chapters)
            if actual_chapters != state.settings.total_chapters:
                old_count = state.settings.total_chapters
                state.settings.total_chapters = actual_chapters
                save_state(state, pipeline.project_dir)
                show_info(f"Updated chapter count: {old_count} → {actual_chapters}")
            show_success("chapter_list.md updated (manual edit).")
            # Go to review section
            need_generation = False
        else:
            brief = user_text if action == ApprovalAction.REGENERATE else ""

    if need_generation:
        while True:
            if brief:
                show_info(f"Regenerating chapter list with guidance: '{brief}'…")
            else:
                show_info(
                    f"Generating chapter list with {stage_cfg.model} (temp={stage_cfg.temperature}, max_tokens={stage_cfg.max_tokens})…"
                )

            prompt = _with_brief(base_list_prompt, brief, "Chapter List")
            brief = ""
            messages = [{"role": "user", "content": prompt}]
            with LLMClient(stage_cfg) as client:
                chapter_list_text = stream_response(
                    client.stream(system, messages), title="Chapter List"
                )
            messages.append({"role": "assistant", "content": chapter_list_text})

            chapters = _parse_chapter_list(chapter_list_text)
            if chapters:
                console.print(f"[dim]Parsed {len(chapters)} chapters.[/dim]")
            # Only warn if there's an explicitly set chapter count that doesn't match
            if total_chapters > 0 and len(chapters) != total_chapters:
                show_warning(
                    f"Expected {total_chapters} chapters but found {len(chapters)} hook entries. "
                    "The output may have been cut short — consider regenerating or approving and editing."
                )

            action, user_text = loop.wait("Chapter List")

            if action == ApprovalAction.APPROVE:
                pipeline.write_file(
                    chapter_list_text, "chapter_outlines/chapter_list.md"
                )
                # Go to review section
                break
            elif action == ApprovalAction.REGENERATE:
                brief = user_text
            elif action == ApprovalAction.FEEDBACK:
                show_info("Incorporating feedback…")
                messages.append({"role": "user", "content": user_text})
                with LLMClient(stage_cfg) as client:
                    chapter_list_text = stream_response(
                        client.stream(system, messages),
                        title="Chapter List",
                        border_style="cyan",
                    )
                messages.append({"role": "assistant", "content": chapter_list_text})
                chapters = _parse_chapter_list(chapter_list_text)
            elif action == ApprovalAction.EDIT:
                chapter_list_text = user_text
                pipeline.write_file(
                    chapter_list_text, "chapter_outlines/chapter_list.md"
                )
                chapters = _parse_chapter_list(chapter_list_text) or [
                    (i, f"Chapter {i}") for i in range(1, total_chapters + 1)
                ]
                # Update chapter count based on actual chapters
                actual_chapters = len(chapters)
                if actual_chapters != state.settings.total_chapters:
                    old_count = state.settings.total_chapters
                    state.settings.total_chapters = actual_chapters
                    save_state(state, pipeline.project_dir)
                    show_info(f"Updated chapter count: {old_count} → {actual_chapters}")
                    show_success("chapter_list.md saved (manual edit).")
                    # Go to review section
                    break
            elif action == ApprovalAction.USE_REVIEW:
                show_info("Regenerating chapter list using review as brief…")
                messages.append({"role": "user", "content": user_text})
                with LLMClient(stage_cfg) as client:
                    chapter_list_text = stream_response(
                        client.stream(system, messages),
                        title="Chapter List",
                        border_style="cyan",
                    )
                messages.append({"role": "assistant", "content": chapter_list_text})
                chapters = _parse_chapter_list(chapter_list_text)
    # Chapter List Review — check for duplicates and structural issues
    # -----------------------------------------------------------------------
    console.print("\n[bold]Reviewing Chapter List for Duplicates[/bold]")

    # Initial review and automatic revision
    show_info("Running chapter list review…")
    list_review = reviewer.review_chapter_list(
        chapter_list_text, story_bible, world_text, character_index
    )
    show_review(list_review)

    # Automatic revision based on review
    show_info("Revising chapter list based on review…")
    revision_prompt = (
        base_list_prompt
        + f"\n\n⚠ CHAPTER LIST REVIEW FEEDBACK:\n{list_review.full_text}\n\n"
        f"REVISION BRIEF: {list_review.revision_brief}\n\n"
    )
    messages = [{"role": "user", "content": revision_prompt}]
    with LLMClient(stage_cfg) as client:
        chapter_list_text = stream_response(
            client.stream(system, messages),
            title="Chapter List (Revised)",
        )
    messages.append({"role": "assistant", "content": chapter_list_text})
    chapters = _parse_chapter_list(chapter_list_text)

    # Final review of revised chapter list
    show_info("Running final chapter list review…")
    list_review = reviewer.review_chapter_list(
        chapter_list_text, story_bible, world_text, character_index
    )
    show_review(list_review)

    if list_review.score < _LOW_SCORE_THRESHOLD:
        show_warning(f"Final review score {list_review.score}/10 is below threshold.")

    console.print(f"[dim]Final review score: {list_review.score}/10[/dim]")

    # Approval loop
    while True:
        action, user_text = loop.wait("Chapter List Review")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(chapter_list_text, "chapter_outlines/chapter_list.md")
            # Update total_chapters in state based on actual chapter count
            actual_chapters = len(chapters)
            if actual_chapters != state.settings.total_chapters:
                old_count = state.settings.total_chapters
                state.settings.total_chapters = actual_chapters
                save_state(state, pipeline.project_dir)
                show_info(f"Updated chapter count: {old_count} → {actual_chapters}")
            show_success("chapter_list.md approved.")
            break
        elif action == ApprovalAction.REGENERATE:
            brief = user_text
            # Regenerate the chapter list
            show_info("Regenerating chapter list with review feedback…")
            revision_prompt = (
                base_list_prompt
                + f"\n\n⚠ CHAPTER LIST REVIEW FEEDBACK:\n{list_review.full_text}\n\n"
                f"REVISION BRIEF: {list_review.revision_brief}\n\n"
            )
            if brief:
                revision_prompt += (
                    f"\n\n⚠ AUTHOR GUIDANCE:\n{brief}\n"
                    f"You MUST incorporate this guidance."
                )
            messages = [{"role": "user", "content": revision_prompt}]
            with LLMClient(stage_cfg) as client:
                chapter_list_text = stream_response(
                    client.stream(system, messages),
                    title="Chapter List (Revised)",
                    border_style="cyan",
                )
            messages.append({"role": "assistant", "content": chapter_list_text})
            chapters = _parse_chapter_list(chapter_list_text)

            # Final review after regeneration
            list_review = reviewer.review_chapter_list(
                chapter_list_text, story_bible, world_text, character_index
            )
            show_review(list_review)
            console.print(f"[dim]Final review score: {list_review.score}/10[/dim]")
        elif action == ApprovalAction.EDIT:
            chapter_list_text = user_text
            pipeline.write_file(chapter_list_text, "chapter_outlines/chapter_list.md")
            chapters = _parse_chapter_list(chapter_list_text) or [
                (i, f"Chapter {i}") for i in range(1, total_chapters + 1)
            ]
            show_success("chapter_list.md updated (manual edit).")
            break
        elif action == ApprovalAction.FEEDBACK:
            # Same as regenerate - incorporate feedback and regenerate
            show_info("Regenerating chapter list with your feedback…")
            revision_prompt = (
                base_list_prompt + f"\n\n⚠ AUTHOR FEEDBACK:\n{user_text}\n\n"
                f"CHAPTER LIST REVIEW FEEDBACK:\n{list_review.full_text}\n\n"
            )
            messages = [{"role": "user", "content": revision_prompt}]
            with LLMClient(stage_cfg) as client:
                chapter_list_text = stream_response(
                    client.stream(system, messages),
                    title="Chapter List (Revised)",
                    border_style="cyan",
                )
            messages.append({"role": "assistant", "content": chapter_list_text})
            chapters = _parse_chapter_list(chapter_list_text)

            # Final review after feedback
            list_review = reviewer.review_chapter_list(
                chapter_list_text, story_bible, world_text, character_index
            )
            show_review(list_review)
            console.print(f"[dim]Final review score: {list_review.score}/10[/dim]")
        elif action == ApprovalAction.USE_REVIEW:
            # Regenerate using the review as brief
            show_info("Regenerating chapter list using review as brief…")
            revision_prompt = (
                base_list_prompt
                + f"\n\n⚠ CHAPTER LIST REVIEW FEEDBACK:\n{list_review.full_text}\n\n"
                f"REVISION BRIEF: {list_review.revision_brief}\n\n"
                f"Write a new chapter list that fully addresses these issues."
            )
            messages = [{"role": "user", "content": revision_prompt}]
            with LLMClient(stage_cfg) as client:
                chapter_list_text = stream_response(
                    client.stream(system, messages),
                    title="Chapter List (Revised)",
                    border_style="cyan",
                )
            messages.append({"role": "assistant", "content": chapter_list_text})
            chapters = _parse_chapter_list(chapter_list_text)

            # Final review after using review
            list_review = reviewer.review_chapter_list(
                chapter_list_text, story_bible, world_text, character_index
            )
            show_review(list_review)
            console.print(f"[dim]Final review score: {list_review.score}/10[/dim]")

    # -----------------------------------------------------------------------
    # Step 4b — Per-chapter outlines
    # -----------------------------------------------------------------------
    console.print(
        f"\n[bold]Step 4b — Chapter Outlines ({len(chapters)} chapters)[/bold]"
    )

    already_approved = set(state.progress.approved_outlines)
    outline_loop = ApprovalLoop(
        allow_skip=True, allow_approve_all=True, allow_use_review=True
    )

    macro_summary = pipeline.read_file("summaries/macro.md")
    approve_all = False

    for chapter_num, chapter_title in chapters:
        outline_path = f"chapter_outlines/chapter_{chapter_num}_outline.md"
        outline_exists_on_disk = (pipeline.project_dir / outline_path).exists()

        # Sync state with disk — file may exist even if state wasn't saved (e.g. after a crash)
        if outline_exists_on_disk and chapter_num not in already_approved:
            state.progress.approved_outlines.append(chapter_num)
            already_approved.add(chapter_num)
            save_state(state, pipeline.project_dir)

        if chapter_num in already_approved:
            show_info(f"Chapter {chapter_num} outline already exists — skipping.")
            continue

        console.print(
            f"\n[bold cyan]Chapter {chapter_num}: {chapter_title}[/bold cyan]"
        )

        # Gather previous outlines for continuity checking
        previous_outlines = _gather_previous_outlines(pipeline, chapter_num, chapters)

        list_entry = _get_chapter_list_entry(chapter_list_text, chapter_num)
        chapter_hook = _get_chapter_hook(chapter_list_text, chapter_num)
        outline_task = pipeline.build_user_prompt(
            "stage4_outline_task.txt",
            chapter_num=str(chapter_num),
            chapter_title=chapter_title,
            chapter_list_entry=list_entry,
            chapter_hook=chapter_hook,
            macro_summary=macro_summary
            or "(no summary yet — this is an early chapter)",
            character_profiles="(see character profiles in characters/ directory)",
        )
        base_outline_prompt = pipeline.build_user_prompt(
            "stage4.txt",
            project_name=state.project_name,
            seed_content=seed_text,
            story_bible_content=story_bible,
            world_content=world_text,
            character_index_content=character_index,
            task=outline_task,
        )

        outline_messages: list[dict] = []
        outline_brief = ""
        outline = ""

        # Auto-approve mode: generate then revise until review score >= 9.0, then save
        _AUTO_APPROVE_THRESHOLD = 9.0
        if approve_all:
            show_info(f"Auto-generating outline for Chapter {chapter_num}…")
            auto_messages = [{"role": "user", "content": base_outline_prompt}]
            with LLMClient(stage_cfg) as client:
                outline = stream_response(
                    client.stream(system, auto_messages),
                    title=f"Chapter {chapter_num} Outline — {chapter_title}",
                )
            auto_messages.append({"role": "assistant", "content": outline})

            _MAX_AUTO_REVISIONS = 3
            revision = 0
            while True:
                show_info("Running outline review…")
                review = reviewer.review_outline(
                    chapter_num,
                    outline,
                    story_bible,
                    character_index,
                    previous_outlines,
                )
                show_review(review)
                if review.score >= _AUTO_APPROVE_SCORE_THRESHOLD:
                    pipeline.write_file(outline, outline_path)
                    state.progress.approved_outlines.append(chapter_num)
                    save_state(state, pipeline.project_dir)
                    show_success(
                        f"{outline_path} saved (auto, score {review.score}/10)."
                    )
                    break
                if revision >= _MAX_AUTO_REVISIONS:
                    show_warning(
                        f"Score {review.score}/10 after {_MAX_AUTO_REVISIONS} auto-revisions — "
                        f"handing back to you."
                    )
                    approve_all = False
                    # Fall through to the normal approval loop with the current outline already generated
                    break
                revision += 1
                show_warning(
                    f"Score {review.score}/10 below {_AUTO_APPROVE_THRESHOLD} — "
                    f"revising (pass {revision}/{_MAX_AUTO_REVISIONS})…"
                )
                auto_messages.append(
                    {
                        "role": "user",
                        "content": f"Please revise based on this review:\n{review.full_text}",
                    }
                )
                with LLMClient(stage_cfg) as client:
                    outline = stream_response(
                        client.stream(system, auto_messages),
                        title=f"Chapter {chapter_num} Outline — {chapter_title} (revision {revision})",
                        border_style="cyan",
                    )
                auto_messages.append({"role": "assistant", "content": outline})

            if approve_all:
                continue
            # Score didn't reach threshold — drop into the normal approval loop below
            # with the best outline generated so far already in `outline` and `review`

        # If we fell through from auto-approve, outline and review are already populated
        fell_through = not approve_all and outline != ""
        outline_messages: list[dict] = (
            [
                {"role": "user", "content": base_outline_prompt},
                {"role": "assistant", "content": outline},
            ]
            if fell_through
            else []
        )
        outline_brief = ""
        if not fell_through:
            outline = ""

        review = None
        while True:
            if not fell_through:
                if outline_brief:
                    show_info(
                        f"Regenerating Chapter {chapter_num} outline with guidance: '{outline_brief}'…"
                    )
                else:
                    show_info(f"Generating outline for Chapter {chapter_num}…")

                outline_prompt = _with_brief(
                    base_outline_prompt, outline_brief, f"Chapter {chapter_num} outline"
                )
                outline_brief = ""
                outline_messages = [{"role": "user", "content": outline_prompt}]
                with LLMClient(stage_cfg) as client:
                    outline = stream_response(
                        client.stream(system, outline_messages),
                        title=f"Chapter {chapter_num} Outline — {chapter_title}",
                    )
                outline_messages.append({"role": "assistant", "content": outline})

                # Inline AI review
                show_info("Running outline review…")
                review = reviewer.review_outline(
                    chapter_num,
                    outline,
                    story_bible,
                    character_index,
                    previous_outlines,
                )
                show_review(review)

            fell_through = False  # only skip generation on the first iteration

            if review and review.score < _LOW_SCORE_THRESHOLD:
                show_warning(
                    f"Review score {review.score}/10 is below threshold. Consider requesting changes."
                )

            action, user_text = outline_loop.wait(
                f"Chapter {chapter_num} — {chapter_title}"
            )

            if action == ApprovalAction.APPROVE:
                pipeline.write_file(outline, outline_path)
                state.progress.approved_outlines.append(chapter_num)
                save_state(state, pipeline.project_dir)
                show_success(f"{outline_path} saved.")
                break
            elif action == ApprovalAction.APPROVE_ALL:
                pipeline.write_file(outline, outline_path)
                state.progress.approved_outlines.append(chapter_num)
                save_state(state, pipeline.project_dir)
                show_success(f"{outline_path} saved.")
                approve_all = True
                break
            elif action == ApprovalAction.SKIP:
                show_info(f"Chapter {chapter_num} outline skipped.")
                break
            elif action == ApprovalAction.REGENERATE:
                outline_brief = user_text
            elif action == ApprovalAction.USE_REVIEW:
                show_info("Regenerating using review as brief…")
                if review:
                    outline_messages.append(
                        {
                            "role": "user",
                            "content": f"Please revise based on this review:\n{review.full_text}",
                        }
                    )
                    with LLMClient(stage_cfg) as client:
                        outline = stream_response(
                            client.stream(system, outline_messages),
                            title=f"Chapter {chapter_num} Outline — {chapter_title}",
                            border_style="cyan",
                        )
                    outline_messages.append({"role": "assistant", "content": outline})

                    # Final review after using review
                    review = _run_final_outline_review(
                        reviewer,
                        chapter_num,
                        outline,
                        story_bible,
                        character_index,
                        previous_outlines,
                    )
                    show_review(review)
            elif action == ApprovalAction.FEEDBACK:
                show_info("Incorporating feedback…")
                outline_messages.append({"role": "user", "content": user_text})
                with LLMClient(stage_cfg) as client:
                    outline = stream_response(
                        client.stream(system, outline_messages),
                        title=f"Chapter {chapter_num} Outline — {chapter_title}",
                        border_style="cyan",
                    )
                outline_messages.append({"role": "assistant", "content": outline})

                # Final review after feedback
                review = _run_final_outline_review(
                    reviewer,
                    chapter_num,
                    outline,
                    story_bible,
                    character_index,
                    previous_outlines,
                )
                show_review(review)
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

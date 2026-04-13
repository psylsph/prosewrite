from __future__ import annotations

import re
from pathlib import Path

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_info, show_review, show_success, show_warning, stream_response, word_count
from ..exceptions import StageError
from ..reviewer import AIReviewer
from ..state import ProjectState, save_state

_LOW_SCORE_THRESHOLD = 7.0
_MAX_MACRO_WORDS = 2000


def assemble_chapter_context(
    chapter_num: int,
    project_dir: Path,
    state: ProjectState,
) -> dict:
    """
    Read all context files needed for chapter writing. Returns a dict of
    string values keyed by placeholder name. Keeps character profiles to
    only those mentioned in the chapter outline to control context size.
    """
    def read(path: str) -> str:
        p = project_dir / path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    outline_text = read(f"chapter_outlines/chapter_{chapter_num}_outline.md")

    # Find which characters appear in this outline
    char_dir = project_dir / "characters"
    character_profiles = ""
    if char_dir.exists():
        for profile_path in sorted(char_dir.glob("*.md")):
            char_name = profile_path.stem.replace("_", " ").lower()
            if char_name in outline_text.lower():
                character_profiles += f"\n\n---\n### {profile_path.stem.replace('_', ' ')}\n"
                character_profiles += profile_path.read_text(encoding="utf-8")

    # Previous chapter for continuity
    prev_chapter = ""
    if chapter_num > 0:
        prev_path = project_dir / f"chapters/chapter_{chapter_num - 1}.md"
        if prev_path.exists():
            prev_chapter = prev_path.read_text(encoding="utf-8")

    return {
        "project_name": state.project_name,
        "chapter_num": str(chapter_num),
        "story_bible_content": read("story_bible.md"),
        "world_content": read("world.md"),
        "macro_summary": read("summaries/macro.md") or "(no summary yet — this is an early chapter)",
        "previous_chapter": prev_chapter or "(this is the first chapter)",
        "chapter_outline": outline_text,
        "character_profiles": character_profiles or "(no matching profiles found — check character directory)",
        "pov": state.settings.pov,
        "tense": state.settings.tense,
        "genre": state.settings.genre,
        "min_words": str(state.settings.min_words_per_chapter),
        "style_notes": "",
    }


def _update_macro_summary(
    pipeline,
    chapter_num: int,
    chapter_text: str,
    state: ProjectState,
) -> None:
    """Append new chapter summary to macro.md, compressing if over limit."""
    existing = pipeline.read_file("summaries/macro.md")
    current_words = word_count(existing)

    stage_cfg = resolve_stage(pipeline.cfg, "chapter_writer")
    system = pipeline.build_system_prompt("chapter_writer")
    summary_prompt = (
        f"Write a 150-200 word narrative summary of the following chapter from {state.project_name}, "
        f"Chapter {chapter_num}. Focus on: what happened, what changed, and what the reader now knows "
        f"that they didn't before. Write in past tense, third person. No chapter headers.\n\n"
        f"CHAPTER:\n{chapter_text[:6000]}"
    )
    with LLMClient(stage_cfg) as client:
        new_summary = client.complete(system, [{"role": "user", "content": summary_prompt}])

    new_summary_block = f"\n\n## Chapter {chapter_num} Summary\n{new_summary.strip()}"

    if current_words + word_count(new_summary_block) > _MAX_MACRO_WORDS:
        compress_prompt = (
            f"The macro summary below is getting long. "
            f"Compress older chapter summaries (keep the last 2-3 chapters as narrative paragraphs, "
            f"convert earlier ones to tight bullet points). "
            f"Preserve all facts; just reduce words. Target: under {_MAX_MACRO_WORDS} words total.\n\n"
            f"CURRENT SUMMARY:\n{existing}\n\nNEW CHAPTER SUMMARY TO APPEND:\n{new_summary_block}"
        )
        with LLMClient(stage_cfg) as client:
            compressed = client.complete(system, [{"role": "user", "content": compress_prompt}])
        pipeline.write_file(compressed, "summaries/macro.md")
    else:
        pipeline.write_file(existing + new_summary_block, "summaries/macro.md")


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 5 — Chapter Writing."""
    total_chapters = state.settings.total_chapters
    if total_chapters == 0:
        raise StageError("total_chapters is 0. Ensure Stage 1 set the chapter count.")

    story_bible = pipeline.read_file("story_bible.md")
    if not story_bible.strip():
        raise StageError("story_bible.md is missing.")

    writer_cfg = resolve_stage(pipeline.cfg, "chapter_writer")
    writer_system = pipeline.build_system_prompt("chapter_writer")
    reviewer = AIReviewer(pipeline)

    approved = set(state.progress.approved_chapters)
    chapter_loop = ApprovalLoop(allow_skip=True)

    for chapter_num in range(0, total_chapters + 1):
        if chapter_num in approved:
            show_info(f"Chapter {chapter_num} already approved — skipping.")
            continue

        outline_path = f"chapter_outlines/chapter_{chapter_num}_outline.md"
        if not (pipeline.project_dir / outline_path).exists():
            show_warning(f"No outline for Chapter {chapter_num} — skipping. Run Stage 4 first.")
            continue

        console.print(f"\n[bold cyan]Chapter {chapter_num} of {total_chapters}[/bold cyan]")

        # --- 1. Context assembly ---
        ctx = assemble_chapter_context(chapter_num, pipeline.project_dir, state)
        ctx["style_notes"] = pipeline.cfg.style.notes

        # --- 2. Internal scene brief (not shown to user) ---
        show_info("Generating internal scene brief…")
        brief_prompt = pipeline.build_user_prompt("stage5_scene_brief.txt", **ctx)
        with LLMClient(writer_cfg) as client:
            scene_brief = client.complete(writer_system, [{"role": "user", "content": brief_prompt}])
        ctx["scene_brief"] = f"INTERNAL SCENE BRIEF:\n{scene_brief}\n---"

        # --- 3. Draft generation ---
        base_write_prompt = pipeline.build_user_prompt("stage5_writer.txt", **ctx)
        show_info(f"Writing draft with {writer_cfg.model} (temp={writer_cfg.temperature})…")
        draft_messages = [{"role": "user", "content": base_write_prompt}]
        with LLMClient(writer_cfg) as client:
            draft = stream_response(
                client.stream(writer_system, draft_messages),
                title=f"Chapter {chapter_num} — Draft",
            )
        draft_messages.append({"role": "assistant", "content": draft})
        draft_wc = word_count(draft)

        # --- 4. AI reviewer pass ---
        show_info("Running AI review…")
        review = reviewer.review_chapter(
            chapter_num=chapter_num,
            chapter_draft=draft,
            chapter_outline=ctx["chapter_outline"],
            character_profiles=ctx["character_profiles"],
            word_count=draft_wc,
        )
        show_review(review)

        # --- 5. Revision ---
        show_info("Revising based on review…")
        base_revision_prompt = pipeline.build_user_prompt(
            "stage5_revision.txt",
            chapter_draft=draft,
            review_text=review.full_text,
            revision_brief=review.revision_brief,
            **{k: v for k, v in ctx.items() if k != "scene_brief"},
        )
        revision_messages = [{"role": "user", "content": base_revision_prompt}]
        with LLMClient(writer_cfg) as client:
            revised = stream_response(
                client.stream(writer_system, revision_messages),
                title=f"Chapter {chapter_num} — Revised",
            )
        revision_messages.append({"role": "assistant", "content": revised})
        revised_wc = word_count(revised)

        # --- 6. Approval loop ---
        if review.score < _LOW_SCORE_THRESHOLD:
            show_warning(f"Review score {review.score}/10 is below threshold.")
        if revised_wc < state.settings.min_words_per_chapter:
            show_warning(
                f"Word count {revised_wc:,} is below minimum {state.settings.min_words_per_chapter:,}."
            )
        console.print(f"[dim]Review score: {review.score}/10  |  Words: {revised_wc:,}[/dim]")

        chapter_brief = ""

        while True:
            action, user_text = chapter_loop.wait(
                "Discuss | 'approve' | 'regenerate: note' | 'use review' | 'skip'"
            )
            chapter_path = f"chapters/chapter_{chapter_num}.md"

            if action == ApprovalAction.APPROVE:
                pipeline.write_file(revised, chapter_path)
                state.progress.approved_chapters.append(chapter_num)
                state.progress.last_approved_chapter = chapter_num
                save_state(state, pipeline.project_dir)
                show_info("Updating macro summary…")
                _update_macro_summary(pipeline, chapter_num, revised, state)
                show_success(f"{chapter_path} saved.")
                break

            elif action == ApprovalAction.SKIP:
                show_info(f"Chapter {chapter_num} skipped.")
                break

            elif action == ApprovalAction.REGENERATE:
                chapter_brief = user_text
                # Full redo from draft
                write_prompt = base_write_prompt
                if chapter_brief:
                    show_info(f"Regenerating draft with guidance: '{chapter_brief}'…")
                    write_prompt += (
                        f"\n\n⚠ AUTHOR GUIDANCE — PRIORITY INSTRUCTION:\n{chapter_brief}\n"
                        f"You MUST incorporate this guidance. It overrides default section structure where needed."
                    )
                    chapter_brief = ""
                else:
                    show_info("Regenerating draft from scratch…")
                draft_messages = [{"role": "user", "content": write_prompt}]
                with LLMClient(writer_cfg) as client:
                    draft = stream_response(
                        client.stream(writer_system, draft_messages),
                        title=f"Chapter {chapter_num} — Draft",
                    )
                draft_messages.append({"role": "assistant", "content": draft})
                draft_wc = word_count(draft)

                show_info("Running AI review on new draft…")
                review = reviewer.review_chapter(
                    chapter_num=chapter_num,
                    chapter_draft=draft,
                    chapter_outline=ctx["chapter_outline"],
                    character_profiles=ctx["character_profiles"],
                    word_count=draft_wc,
                )
                show_review(review)

                show_info("Revising based on review…")
                revision_prompt = pipeline.build_user_prompt(
                    "stage5_revision.txt",
                    chapter_draft=draft,
                    review_text=review.full_text,
                    revision_brief=review.revision_brief,
                    **{k: v for k, v in ctx.items() if k != "scene_brief"},
                )
                revision_messages = [{"role": "user", "content": revision_prompt}]
                with LLMClient(writer_cfg) as client:
                    revised = stream_response(
                        client.stream(writer_system, revision_messages),
                        title=f"Chapter {chapter_num} — Revised",
                    )
                revision_messages.append({"role": "assistant", "content": revised})
                revised_wc = word_count(revised)
                console.print(f"[dim]Review score: {review.score}/10  |  Words: {revised_wc:,}[/dim]")

            elif action == ApprovalAction.USE_REVIEW:
                show_info("Regenerating using review as brief…")
                use_review_prompt = (
                    f"{base_write_prompt}\n\n"
                    f"IMPORTANT: A review of your previous draft identified these issues:\n"
                    f"{review.summary}\n\nRevision brief: {review.revision_brief}\n\n"
                    f"Write a new draft that fully addresses these issues."
                )
                use_review_messages = [{"role": "user", "content": use_review_prompt}]
                with LLMClient(writer_cfg) as client:
                    revised = stream_response(
                        client.stream(writer_system, use_review_messages),
                        title=f"Chapter {chapter_num} — Use-Review Draft",
                        border_style="cyan",
                    )
                revision_messages = [{"role": "user", "content": use_review_prompt},
                                     {"role": "assistant", "content": revised}]
                revised_wc = word_count(revised)
                console.print(f"[dim]Words: {revised_wc:,}[/dim]")

            elif action == ApprovalAction.FEEDBACK:
                show_info("Incorporating feedback…")
                revision_messages.append({"role": "user", "content": user_text})
                with LLMClient(writer_cfg) as client:
                    revised = stream_response(
                        client.stream(writer_system, revision_messages),
                        title=f"Chapter {chapter_num} — Feedback",
                        border_style="cyan",
                    )
                revision_messages.append({"role": "assistant", "content": revised})
                revised_wc = word_count(revised)
                console.print(f"[dim]Words: {revised_wc:,}[/dim]")

            elif action == ApprovalAction.EDIT:
                revised = user_text
                pipeline.write_file(revised, chapter_path)
                state.progress.approved_chapters.append(chapter_num)
                state.progress.last_approved_chapter = chapter_num
                save_state(state, pipeline.project_dir)
                show_info("Updating macro summary…")
                _update_macro_summary(pipeline, chapter_num, revised, state)
                show_success(f"{chapter_path} saved (manual edit).")
                break

    state.current_stage = "stage6_export"
    save_state(state, pipeline.project_dir)
    return state

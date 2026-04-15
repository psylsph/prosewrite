from __future__ import annotations

from pathlib import Path
from itertools import islice

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import (
    console,
    show_info,
    show_review,
    show_success,
    show_warning,
    word_count,
)
from ..reviewer import AIReviewer
from ..state import ProjectState, save_state


def _chapter_num(p: Path) -> int:
    import re

    m = re.search(r"chapter_(\d+)", p.name)
    return int(m.group(1)) if m else 9999


def _create_batches(
    chapters: list[int], batch_size: int = 5, overlap: int = 2
) -> list[list[int]]:
    """Create overlapping batches of chapter numbers.

    Args:
        chapters: Sorted list of chapter numbers
        batch_size: Number of chapters per batch
        overlap: Number of overlapping chapters between batches

    Example:
        chapters = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        batch_size = 5, overlap = 2
        → [[0,1,2,3,4], [3,4,5,6,7], [6,7,8,9]]
    """
    if not chapters:
        return []

    batches: list[list[int]] = []
    i = 0
    while i < len(chapters):
        batch = list(islice(chapters, i, i + batch_size))
        if not batch:
            break
        batches.append(batch)
        next_i = i + batch_size - overlap
        # Check if next batch would bring enough new chapters
        # If next batch would overlap by more than intended, stop
        if next_i >= len(chapters):
            # Next batch would start past the end
            break
        if len(chapters) - next_i <= overlap:
            # Next batch would have overlap or fewer new chapters
            break
        i = next_i

    return batches


def _read_chapters(project_dir: Path, chapter_nums: list[int]) -> dict[int, str]:
    """Read chapter texts into a dict."""
    chapters = {}
    for num in chapter_nums:
        path = project_dir / f"chapters/chapter_{num}.md"
        if path.exists():
            chapters[num] = path.read_text(encoding="utf-8")
    return chapters


def _format_batch_content(chapters: dict[int, str]) -> str:
    """Format chapters for the review prompt."""
    parts = []
    for num in sorted(chapters.keys()):
        label = "Prelude" if num == 0 else f"Chapter {num}"
        parts.append(f"## {label}\n\n{chapters[num]}")
    return "\n\n---\n\n".join(parts)


def _run_continuity_review(
    pipeline,
    chapter_nums: list[int],
    state: ProjectState,
) -> tuple[float, str, str]:
    """Run continuity review on a batch of chapters.

    Returns:
        (score, issues_text, revision_brief)
    """
    import re

    stage_cfg = resolve_stage(pipeline.cfg, "continuity_reviewer")
    system = pipeline.build_system_prompt("continuity_reviewer")

    chapters = _read_chapters(pipeline.project_dir, chapter_nums)
    batch_content = _format_batch_content(chapters)

    story_bible = pipeline.read_file("story_bible.md")
    character_index = pipeline.read_file("character_index.md")
    world_content = pipeline.read_file("world.md")

    chapter_range = f"{chapter_nums[0]}-{chapter_nums[-1]}"

    user_prompt = pipeline.build_user_prompt(
        "stage6_continuity.txt",
        project_name=state.project_name,
        chapter_range=chapter_range,
        batch_content=batch_content,
        story_bible_content=story_bible[:5000],
        character_index_content=character_index[:3000],
        world_content=world_content[:3000],
    )

    with LLMClient(stage_cfg) as client:
        review_text = client.complete(
            system, [{"role": "user", "content": user_prompt}]
        )

    # Extract score
    match = re.search(
        r"OVERALL CONTINUITY SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10",
        review_text,
        re.IGNORECASE,
    )
    score = float(match.group(1)) if match else 8.0

    # Extract issues section
    match = re.search(
        r"\*\*ISSUES FOUND:\*\*\s*:?\s*(.*?)(?:\*\*REVISION BRIEF|\Z)",
        review_text,
        re.DOTALL | re.IGNORECASE,
    )
    issues = match.group(1).strip() if match else "No issues section found"

    # Extract revision brief
    match = re.search(
        r"\*\*REVISION BRIEF:\*\*\s*:?\s*(.*?)\s*$",
        review_text,
        re.DOTALL | re.IGNORECASE,
    )
    brief = match.group(1).strip() if match else ""

    return score, issues, brief


def _run_quality_review(
    pipeline,
    reviewer: AIReviewer,
    chapter_nums: list[int],
    state: ProjectState,
) -> list[tuple[int, float, str]]:
    """Run full quality review on each chapter in batch.

    Returns:
        List of (chapter_num, score, summary) tuples
    """
    results = []
    chapters = _read_chapters(pipeline.project_dir, chapter_nums)

    for num in sorted(chapters.keys()):
        chapter_text = chapters[num]

        # Read outline and character profiles for this chapter
        outline_path = (
            pipeline.project_dir / f"chapter_outlines/chapter_{num}_outline.md"
        )
        outline = (
            outline_path.read_text(encoding="utf-8") if outline_path.exists() else ""
        )

        # Get character profiles mentioned in outline
        char_dir = pipeline.project_dir / "characters"
        character_profiles = ""
        if char_dir.exists():
            for profile_path in sorted(char_dir.glob("*.md")):
                char_name = profile_path.stem.replace("_", " ").lower()
                if char_name in outline.lower():
                    character_profiles += (
                        f"\n\n---\n### {profile_path.stem.replace('_', ' ')}\n"
                    )
                    character_profiles += profile_path.read_text(encoding="utf-8")

        review = reviewer.review_chapter(
            chapter_num=num,
            chapter_draft=chapter_text,
            chapter_outline=outline,
            character_profiles=character_profiles,
            word_count=word_count(chapter_text),
        )

        results.append((num, review.score, review.summary))

    return results


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 6 — Batch review of completed chapters."""
    chapters_dir = pipeline.project_dir / "chapters"
    chapter_files = (
        sorted(chapters_dir.glob("chapter_*.md"), key=_chapter_num)
        if chapters_dir.exists()
        else []
    )

    if not chapter_files:
        show_warning("No chapters found to review.")
        state.current_stage = "stage7_export"
        save_state(state, pipeline.project_dir)
        return state

    chapter_nums = [_chapter_num(p) for p in chapter_files]
    total_chapters = len(chapter_nums)

    console.print(f"\n[bold cyan]Batch Review[/bold cyan]")
    console.print(
        f"[dim]Reviewing {total_chapters} chapters in overlapping batches of 5[/dim]"
    )

    # Create overlapping batches
    batches = _create_batches(chapter_nums, batch_size=5, overlap=2)
    total_batches = len(batches)

    show_info(f"Created {total_batches} batches with 2-chapter overlap")

    reviewer = AIReviewer(pipeline)
    batch_loop = ApprovalLoop(allow_skip=True, allow_approve_all=True)
    auto_approve_mode = False

    approved_batches: list[list[int]] = []

    for batch_idx, chapter_nums_batch in enumerate(batches, 1):
        batch_range = f"{chapter_nums_batch[0]}-{chapter_nums_batch[-1]}"

        console.print(
            f"\n[bold cyan]Batch {batch_idx}/{total_batches}[/bold cyan] ([dim]Chapters {batch_range}[/dim])"
        )

        # Skip if all chapters in this batch were already approved
        if all(num in approved_batches for num in chapter_nums_batch):
            show_info(f"Batch {batch_idx} already reviewed — skipping.")
            continue

        # Step 1: Continuity review
        show_info(f"Running continuity review on chapters {batch_range}…")
        continuity_score, issues_text, revision_brief = _run_continuity_review(
            pipeline, chapter_nums_batch, state
        )

        console.print(f"[dim]Continuity score: {continuity_score}/10[/dim]")

        needs_quality_review = continuity_score < 9.0 or "MAJOR" in issues_text.upper()

        quality_results: list[tuple[int, float, str]] = []

        if needs_quality_review:
            show_info("Issues found — running full quality review on batch…")
            quality_results = _run_quality_review(
                pipeline, reviewer, chapter_nums_batch, state
            )

            # Show quality review results
            for num, score, summary in quality_results:
                console.print(f"[dim]Chapter {num}: {score}/10[/dim]")

        # Display results summary
        if needs_quality_review:
            console.print(f"\n[bold yellow]Continuity Issues:[/bold yellow]")
            console.print(issues_text[:500])

            console.print(f"\n[bold yellow]Quality Review Summary:[/bold yellow]")
            avg_score = sum(s for _, s, _ in quality_results) / len(quality_results)
            console.print(f"[dim]Average score: {avg_score:.1f}/10[/dim]")
        else:
            console.print(f"[green]✓ Continuity check passed — no issues found[/green]")

        # Approval loop
        if auto_approve_mode:
            if continuity_score >= 9.0 and not needs_quality_review:
                approved_batches.extend(chapter_nums_batch)
                show_success(f"Batch {batch_idx} auto-approved.")
                continue
            else:
                auto_approve_mode = False
                show_warning(
                    f"Auto-approve disabled: batch score below threshold or issues found."
                )

        while True:
            action, user_text = batch_loop.wait(
                f"Batch {batch_idx} (Chapters {batch_range})"
            )

            if action == ApprovalAction.APPROVE:
                approved_batches.extend(chapter_nums_batch)
                show_success(f"Batch {batch_idx} approved.")
                break

            elif action == ApprovalAction.APPROVE_ALL:
                if continuity_score < 9.0 or needs_quality_review:
                    show_warning(
                        f"Cannot enable auto-approve: current batch has issues or score below 9.0."
                    )
                    continue
                auto_approve_mode = True
                show_info("Auto-approve enabled for remaining batches.")
                approved_batches.extend(chapter_nums_batch)
                show_success(f"Batch {batch_idx} auto-approved.")
                break

            elif action == ApprovalAction.SKIP:
                show_info(f"Batch {batch_idx} skipped.")
                break

            elif action == ApprovalAction.REGENERATE:
                # Identify problematic chapters
                problem_chapters = []
                if needs_quality_review:
                    for num, score, summary in quality_results:
                        if score < 8.0:
                            problem_chapters.append((num, score))
                elif continuity_score < 8.0:
                    problem_chapters = [
                        (n, continuity_score) for n in chapter_nums_batch
                    ]

                show_info(
                    f"Regenerate mode: {len(problem_chapters)} chapters need attention"
                )
                for num, score in problem_chapters:
                    console.print(f"  [dim]• Chapter {num} (score: {score}/10)[/dim]")

                show_info("\nTo regenerate these chapters:")
                console.print(
                    f"  Edit project_state.json and remove chapters from 'approved_chapters' list"
                )
                console.print(
                    f"  Then run: prosewrite resume --name {state.project_name}"
                )
                console.print(
                    f"  Or: prosewrite run --name {state.project_name} --stage chapters\n"
                )

                show_info(f"Revision brief for this batch:\n{revision_brief[:400]}")
                # Re-show the menu
                continue

            elif action == "_regenerate_guided":
                # User selected "Regenerate with guidance" - user_text contains their guidance
                # Identify problematic chapters
                problem_chapters = []
                if needs_quality_review:
                    for num, score, summary in quality_results:
                        if score < 8.0:
                            problem_chapters.append((num, score))
                elif continuity_score < 8.0:
                    problem_chapters = [
                        (n, continuity_score) for n in chapter_nums_batch
                    ]

                show_info(
                    f"Regenerate mode: {len(problem_chapters)} chapters need attention"
                )
                for num, score in problem_chapters:
                    console.print(f"  [dim]• Chapter {num} (score: {score}/10)[/dim]")

                # Save user's guidance to a file for use during regeneration
                guidance_file = (
                    pipeline.project_dir / f"regen_guidance_batch{batch_idx}.md"
                )
                guidance_content = f"""# Regeneration Guidance — Batch {batch_idx} (Chapters {chapter_range})

## Your Guidance
{user_text}

## Revision Brief
{revision_brief}

## Chapters to Regenerate
"""
                if needs_quality_review:
                    guidance_content += "\n### Quality Issues\n"
                    for num, score, summary in quality_results:
                        if score < 8.0:
                            guidance_content += (
                                f"\n**Chapter {num}** (score: {score}/10):\n{summary}\n"
                            )
                if continuity_score < 8.0:
                    guidance_content += f"\n### Continuity Issues (score: {continuity_score}/10)\n{issues_text}\n"

                guidance_file.write_text(guidance_content, encoding="utf-8")
                show_success(f"Guidance saved to {guidance_file.name}")

                show_info("\nTo regenerate with this guidance:")
                console.print(
                    f"  1. Edit project_state.json and remove chapters from 'approved_chapters' list"
                )
                console.print(
                    f"  2. Run: prosewrite resume --name {state.project_name}"
                )
                console.print(f"  3. Reference the guidance file when revising\n")
                # Re-show the menu
                continue

            elif action == ApprovalAction.FEEDBACK:
                # Identify problematic chapters
                problem_chapters = []
                if needs_quality_review:
                    for num, score, summary in quality_results:
                        if score < 8.0:
                            problem_chapters.append((num, score))
                elif continuity_score < 8.0:
                    problem_chapters = [
                        (n, continuity_score) for n in chapter_nums_batch
                    ]

                show_info(
                    f"To fix issues in this batch, you can regenerate specific chapters."
                )
                for num, score in problem_chapters:
                    console.print(f"  [dim]• Chapter {num} (score: {score}/10)[/dim]")

                # Save user feedback to a file for use during regeneration
                feedback_file = (
                    pipeline.project_dir
                    / f"batch_feedback_batch{batch_idx}_ch{'-'.join(str(n) for n in chapter_nums_batch)}.md"
                )
                feedback_content = f"""# Batch Feedback — Chapters {chapter_range}

## Revision Brief
{revision_brief}

## Your Notes
{user_text}

## Issue Details
"""
                if needs_quality_review:
                    feedback_content += "\n### Quality Issues\n"
                    for num, score, summary in quality_results:
                        if score < 8.0:
                            feedback_content += (
                                f"\n**Chapter {num}** (score: {score}/10):\n{summary}\n"
                            )
                if continuity_score < 8.0:
                    feedback_content += f"\n### Continuity Issues (score: {continuity_score}/10)\n{issues_text}\n"

                feedback_file.write_text(feedback_content, encoding="utf-8")
                show_success(f"Feedback saved to {feedback_file.name}")

                show_info("\nTo regenerate with this feedback:")
                console.print("  1. Note which chapters you want to fix")
                console.print("  2. Run: prosewrite resume --name <project>")
                console.print("  3. Use the feedback file when revising\n")
                # Re-show the menu
                continue

            elif action == ApprovalAction.EDIT:
                show_info("Batch review doesn't support direct editing.")
                console.print("\nTo fix chapters:")
                console.print(
                    "  Option 1: Edit chapter files directly in projects/<name>/chapters/"
                )
                console.print(
                    "  Option 2: Use Regenerate to regenerate with AI assistance"
                )
                console.print(
                    "  Option 3: Use Feedback to save guidance for regeneration\n"
                )
                # Re-show the menu
                continue

    show_success("Batch review complete.")
    state.current_stage = "stage7_export"
    save_state(state, pipeline.project_dir)
    return state

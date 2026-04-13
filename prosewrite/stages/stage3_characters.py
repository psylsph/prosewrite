from __future__ import annotations

import re
from pathlib import Path

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_draft, show_info, show_success, show_warning, word_count
from ..exceptions import StageError
from ..state import ProjectState, save_state


def _parse_character_names(index_text: str) -> list[str]:
    """
    Parse character names from a Markdown table. Returns a list of full names.
    The first column is assumed to be the character name; header row is skipped.
    Falls back gracefully if the table format is non-standard.
    """
    names: list[str] = []
    in_table = False
    header_skipped = False

    for line in index_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break  # table ended
            continue

        in_table = True
        # Skip separator row (|---|---|)
        if re.match(r"^\|[-| :]+\|$", stripped):
            header_skipped = True
            continue
        # Skip the header row (first non-separator pipe row)
        if not header_skipped:
            header_skipped = True
            continue

        # Extract first column
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if parts and parts[0]:
            name = parts[0].strip()
            if name and name.lower() not in ("name", "character"):
                names.append(name)

    return names


def _get_index_entry(index_text: str, character_name: str) -> str:
    """Extract a character's table row from the index."""
    for line in index_text.splitlines():
        if character_name.lower() in line.lower() and line.strip().startswith("|"):
            return line.strip()
    return f"| {character_name} | (see index) | (see index) |"


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 3 — Characters (Step 3a: index, Step 3b: per-character profiles)."""
    seed_text = pipeline.read_file("seed.md")
    story_bible = pipeline.read_file("story_bible.md")
    world_text = pipeline.read_file("world.md")

    if not story_bible.strip():
        raise StageError("story_bible.md is missing. Run Stage 1 first.")

    stage_cfg = resolve_stage(pipeline.cfg, "characters")
    system = pipeline.build_system_prompt("characters")

    # -----------------------------------------------------------------------
    # Step 3a — Character Index
    # -----------------------------------------------------------------------
    console.print("\n[bold]Step 3a — Character Index[/bold]")

    index_task = pipeline.build_user_prompt("stage3_index_task.txt")
    base_prompt = pipeline.build_user_prompt(
        "stage3.txt",
        project_name=state.project_name,
        seed_content=seed_text,
        story_bible_content=story_bible,
        world_content=world_text,
        task=index_task,
    )

    loop = ApprovalLoop()
    character_index = ""

    while True:
        show_info(f"Generating character index with {stage_cfg.model} (temp={stage_cfg.temperature})…")
        with LLMClient(stage_cfg) as client:
            character_index = client.complete(system, [{"role": "user", "content": base_prompt}])

        show_draft(character_index, title="Character Index")

        names = _parse_character_names(character_index)
        if names:
            console.print(f"[dim]Detected {len(names)} characters: {', '.join(names)}[/dim]")
        else:
            show_warning(
                "Could not parse character names from the index table.\n"
                "Ensure the output is a Markdown table with character names in the first column."
            )

        action, user_text = loop.wait("Approve index, request changes, or type 'redo'")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(character_index, "character_index.md")
            show_success("character_index.md saved.")
            break
        elif action == ApprovalAction.REGENERATE:
            continue
        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            with LLMClient(stage_cfg) as client:
                character_index = client.complete(system, [
                    {"role": "user", "content": base_prompt},
                    {"role": "assistant", "content": character_index},
                    {"role": "user", "content": user_text},
                ])
            continue
        elif action == ApprovalAction.EDIT:
            character_index = user_text
            pipeline.write_file(character_index, "character_index.md")
            show_success("character_index.md saved (manual edit).")
            break

    # Re-parse names after any edits
    names = _parse_character_names(character_index)
    if not names:
        show_warning("No character names could be parsed. Prompting for manual entry.")
        from rich.prompt import Prompt
        raw = Prompt.ask("Enter character names separated by commas")
        names = [n.strip() for n in raw.split(",") if n.strip()]

    # -----------------------------------------------------------------------
    # Step 3b — Per-character profiles
    # -----------------------------------------------------------------------
    console.print(f"\n[bold]Step 3b — Character Profiles ({len(names)} characters)[/bold]")

    profile_loop = ApprovalLoop(allow_skip=True)

    for i, name in enumerate(names, 1):
        console.print(f"\n[bold cyan][{i}/{len(names)}] {name}[/bold cyan]")

        index_entry = _get_index_entry(character_index, name)
        profile_task = pipeline.build_user_prompt(
            "stage3_profile_task.txt",
            character_name=name,
            character_index_entry=index_entry,
        )
        profile_prompt = pipeline.build_user_prompt(
            "stage3.txt",
            project_name=state.project_name,
            seed_content=seed_text,
            story_bible_content=story_bible,
            world_content=world_text,
            task=profile_task,
        )

        while True:
            show_info(f"Writing profile for {name}…")
            with LLMClient(stage_cfg) as client:
                profile = client.complete(system, [{"role": "user", "content": profile_prompt}])

            show_draft(profile, title=f"Character Profile — {name}", word_count=word_count(profile))

            action, user_text = profile_loop.wait(
                f"Approve {name}'s profile, request changes, 'redo', or 'skip'"
            )

            safe_name = re.sub(r"[^\w\-]", "_", name)
            profile_path = f"characters/{safe_name}.md"

            if action == ApprovalAction.APPROVE:
                pipeline.write_file(profile, profile_path)
                show_success(f"{profile_path} saved.")
                break
            elif action == ApprovalAction.SKIP:
                show_info(f"Skipped {name}.")
                break
            elif action == ApprovalAction.REGENERATE:
                continue
            elif action == ApprovalAction.FEEDBACK:
                show_info("Incorporating feedback…")
                with LLMClient(stage_cfg) as client:
                    profile = client.complete(system, [
                        {"role": "user", "content": profile_prompt},
                        {"role": "assistant", "content": profile},
                        {"role": "user", "content": user_text},
                    ])
                continue
            elif action == ApprovalAction.EDIT:
                profile = user_text
                pipeline.write_file(profile, profile_path)
                show_success(f"{profile_path} saved (manual edit).")
                break

    state.current_stage = "stage4_outlines"
    save_state(state, pipeline.project_dir)
    return state

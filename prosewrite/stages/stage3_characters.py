from __future__ import annotations

import re
from pathlib import Path

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_info, show_success, show_warning, stream_response
from ..exceptions import StageError
from ..state import ProjectState, save_state


def _parse_character_names(index_text: str) -> list[str]:
    names: list[str] = []
    in_table = False
    header_skipped = False

    for line in index_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        if re.match(r"^\|[-| :]+\|$", stripped):
            header_skipped = True
            continue
        if not header_skipped:
            header_skipped = True
            continue
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if parts and parts[0]:
            name = parts[0].strip()
            if name and name.lower() not in ("name", "character"):
                names.append(name)

    return names


def _get_index_entry(index_text: str, character_name: str) -> str:
    for line in index_text.splitlines():
        if character_name.lower() in line.lower() and line.strip().startswith("|"):
            return line.strip()
    return f"| {character_name} | (see index) | (see index) |"


def _with_brief(prompt: str, brief: str, context: str = "output") -> str:
    if brief:
        return (
            prompt
            + f"\n\n⚠ AUTHOR GUIDANCE — PRIORITY INSTRUCTION:\n{brief}\n"
            f"You MUST incorporate this guidance. It overrides default section structure where needed."
        )
    return prompt


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
    messages: list[dict] = []
    brief = ""

    while True:
        if brief:
            show_info(f"Regenerating character index with guidance: '{brief}'…")
        else:
            show_info(f"Generating character index with {stage_cfg.model} (temp={stage_cfg.temperature})…")

        prompt = _with_brief(base_prompt, brief, "Character Index")
        brief = ""
        messages = [{"role": "user", "content": prompt}]
        with LLMClient(stage_cfg) as client:
            character_index = stream_response(
                client.stream(system, messages), title="Character Index"
            )
        messages.append({"role": "assistant", "content": character_index})

        names = _parse_character_names(character_index)
        if names:
            console.print(f"[dim]Detected {len(names)} characters: {', '.join(names)}[/dim]")
        else:
            show_warning(
                "Could not parse character names from the index table.\n"
                "Ensure the output is a Markdown table with character names in the first column."
            )

        action, user_text = loop.wait(
            "Discuss | 'approve' | 'regenerate' for fresh start | 'regenerate: your note' to rewrite with guidance"
        )

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(character_index, "character_index.md")
            show_success("character_index.md saved.")
            break
        elif action == ApprovalAction.REGENERATE:
            brief = user_text
        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            messages.append({"role": "user", "content": user_text})
            with LLMClient(stage_cfg) as client:
                character_index = stream_response(
                    client.stream(system, messages), title="Character Index", border_style="cyan"
                )
            messages.append({"role": "assistant", "content": character_index})
        elif action == ApprovalAction.EDIT:
            character_index = user_text
            pipeline.write_file(character_index, "character_index.md")
            show_success("character_index.md saved (manual edit).")
            break

    # Re-parse after any edits
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
        base_profile_prompt = pipeline.build_user_prompt(
            "stage3.txt",
            project_name=state.project_name,
            seed_content=seed_text,
            story_bible_content=story_bible,
            world_content=world_text,
            task=profile_task,
        )

        profile_messages: list[dict] = []
        profile_brief = ""

        while True:
            if profile_brief:
                show_info(f"Regenerating {name}'s profile with guidance: '{profile_brief}'…")
            else:
                show_info(f"Writing profile for {name}…")

            profile_prompt = _with_brief(base_profile_prompt, profile_brief, f"{name}'s profile")
            profile_brief = ""
            profile_messages = [{"role": "user", "content": profile_prompt}]
            with LLMClient(stage_cfg) as client:
                profile = stream_response(
                    client.stream(system, profile_messages),
                    title=f"Character Profile — {name}",
                )
            profile_messages.append({"role": "assistant", "content": profile})

            action, user_text = profile_loop.wait(
                f"Discuss | 'approve' | 'regenerate: note' | 'skip'"
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
                profile_brief = user_text
            elif action == ApprovalAction.FEEDBACK:
                show_info("Incorporating feedback…")
                profile_messages.append({"role": "user", "content": user_text})
                with LLMClient(stage_cfg) as client:
                    profile = stream_response(
                        client.stream(system, profile_messages),
                        title=f"Character Profile — {name}",
                        border_style="cyan",
                    )
                profile_messages.append({"role": "assistant", "content": profile})
            elif action == ApprovalAction.EDIT:
                profile = user_text
                pipeline.write_file(profile, profile_path)
                show_success(f"{profile_path} saved (manual edit).")
                break

    state.current_stage = "stage4_outlines"
    save_state(state, pipeline.project_dir)
    return state

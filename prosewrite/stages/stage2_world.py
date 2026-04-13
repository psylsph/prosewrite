from __future__ import annotations

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_draft, show_info, show_success, word_count
from ..exceptions import StageError
from ..state import ProjectState, save_state


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 2 — World Builder."""
    seed_text = pipeline.read_file("seed.md")
    story_bible = pipeline.read_file("story_bible.md")
    if not story_bible.strip():
        raise StageError("story_bible.md is missing. Run Stage 1 first.")

    stage_cfg = resolve_stage(pipeline.cfg, "world_builder")
    system = pipeline.build_system_prompt("world_builder")
    loop = ApprovalLoop()

    while True:
        show_info(f"Building world with {stage_cfg.model} (temp={stage_cfg.temperature})…")
        user_prompt = pipeline.build_user_prompt(
            "stage2.txt",
            project_name=state.project_name,
            seed_content=seed_text,
            story_bible_content=story_bible,
        )
        with LLMClient(stage_cfg) as client:
            world = client.complete(system, [{"role": "user", "content": user_prompt}])

        show_draft(world, title="World Guide", word_count=word_count(world))

        action, user_text = loop.wait("Approve world guide, request changes, or type 'redo'")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(world, "world.md")
            show_success("world.md saved.")
            state.current_stage = "stage3_characters"
            save_state(state, pipeline.project_dir)
            return state

        elif action == ApprovalAction.REGENERATE:
            console.print("[dim]Regenerating…[/dim]")
            continue

        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            with LLMClient(stage_cfg) as client:
                world = client.complete(system, [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": world},
                    {"role": "user", "content": user_text},
                ])
            continue

        elif action == ApprovalAction.EDIT:
            world = user_text
            pipeline.write_file(world, "world.md")
            state.current_stage = "stage3_characters"
            save_state(state, pipeline.project_dir)
            show_success("world.md saved (manual edit).")
            return state

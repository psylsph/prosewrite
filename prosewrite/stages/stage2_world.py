from __future__ import annotations

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import show_info, show_success, stream_response
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

    messages: list[dict] = []
    brief = ""

    while True:
        if brief:
            show_info(f"Regenerating with guidance: '{brief}'…")
        else:
            show_info(f"Building world with {stage_cfg.model} (temp={stage_cfg.temperature})…")

        guidance = (
            f"\n\n⚠ AUTHOR GUIDANCE — PRIORITY INSTRUCTION:\n{brief}\n"
            f"You MUST incorporate this guidance. It overrides default section structure where needed.\n"
        ) if brief else ""
        brief = ""
        user_prompt = pipeline.build_user_prompt(
            "stage2.txt",
            project_name=state.project_name,
            seed_content=seed_text,
            story_bible_content=story_bible,
        ) + guidance

        messages = [{"role": "user", "content": user_prompt}]
        with LLMClient(stage_cfg) as client:
            world = stream_response(client.stream(system, messages), title="World Guide")
        messages.append({"role": "assistant", "content": world})

        action, user_text = loop.wait("World Guide")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(world, "world.md")
            show_success("world.md saved.")
            state.current_stage = "stage3_characters"
            save_state(state, pipeline.project_dir)
            return state

        elif action == ApprovalAction.REGENERATE:
            brief = user_text

        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            messages.append({"role": "user", "content": user_text})
            with LLMClient(stage_cfg) as client:
                world = stream_response(
                    client.stream(system, messages), title="World Guide", border_style="cyan"
                )
            messages.append({"role": "assistant", "content": world})

        elif action == ApprovalAction.EDIT:
            world = user_text
            pipeline.write_file(world, "world.md")
            state.current_stage = "stage3_characters"
            save_state(state, pipeline.project_dir)
            show_success("world.md saved (manual edit).")
            return state

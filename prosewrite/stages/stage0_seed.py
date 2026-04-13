from __future__ import annotations

from ..approval import ApprovalAction, ApprovalLoop
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_draft, show_info, show_success, show_warning, word_count
from ..exceptions import StageError
from ..state import ProjectState, save_state


def run(pipeline, state: ProjectState) -> ProjectState:
    """Stage 0 — Seed Analysis. Reference implementation for all stages."""
    seed_text = pipeline.read_file("seed.md")
    if not seed_text.strip():
        raise StageError("seed.md is empty or missing. Add your premise before running Stage 0.")

    stage_cfg = resolve_stage(pipeline.cfg, "seed_analysis")
    system = pipeline.build_system_prompt("seed_analysis")
    loop = ApprovalLoop(allow_skip=False)

    while True:
        show_info(f"Analysing seed with {stage_cfg.model} (temp={stage_cfg.temperature})…")
        user_prompt = pipeline.build_user_prompt(
            "stage0.txt",
            project_name=state.project_name,
            seed_content=seed_text,
        )
        with LLMClient(stage_cfg) as client:
            analysis = client.complete(system, [{"role": "user", "content": user_prompt}])

        show_draft(analysis, title="Seed Analysis", word_count=word_count(analysis))

        action, user_text = loop.wait("Approve this analysis, ask for changes, or type 'redo'")

        if action == ApprovalAction.APPROVE:
            pipeline.write_file(analysis, "seed_analysis.md")
            show_success("seed_analysis.md saved.")
            state.current_stage = "stage1_bible"
            save_state(state, pipeline.project_dir)
            return state

        elif action == ApprovalAction.REGENERATE:
            console.print("[dim]Regenerating…[/dim]")
            continue

        elif action == ApprovalAction.FEEDBACK:
            show_info("Incorporating feedback…")
            with LLMClient(stage_cfg) as client:
                analysis = client.complete(system, [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": analysis},
                    {"role": "user", "content": user_text},
                ])
            continue

        elif action == ApprovalAction.EDIT:
            analysis = user_text
            pipeline.write_file(analysis, "seed_analysis.md")
            show_success("seed_analysis.md saved (manual edit).")
            state.current_stage = "stage1_bible"
            save_state(state, pipeline.project_dir)
            return state

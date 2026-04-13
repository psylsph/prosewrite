from __future__ import annotations

from pathlib import Path

import questionary

from ..approval import ApprovalAction, ApprovalLoop, _STYLE
from ..client import LLMClient
from ..config import resolve_stage
from ..display import console, show_info, show_success, stream_response, word_count
from ..exceptions import StageError
from ..state import ProjectState, save_state

# ── Prompts ───────────────────────────────────────────────────────────────────

_CONSOLIDATE_PROMPT = (
    "Based on our conversation so far, write the final, consolidated seed analysis document. "
    "Incorporate every point we discussed and agreed on. "
    "Use the same structured format as the original analysis (the eight numbered sections). "
    "This is the document that will be saved — make it complete and self-contained."
)

_CONFIRM_DECISIONS_PROMPT = (
    "Before we write the updated seed, list the key decisions we agreed on in this session "
    "as a numbered summary (e.g. '1. Antagonist now has X motivation instead of Y'). "
    "Do not write the seed yet — just list the agreed changes so the author can confirm."
)

_GENERATE_SEED_PROMPT = (
    "Now write the complete, updated seed document. "
    "Incorporate every change we agreed on. "
    "Match the style, voice, and format of the original seed — this replaces it. "
    "Write only the seed text. No preamble, no commentary after."
)

_APPLY_KEYWORDS = (
    "write it", "apply", "generate", "update seed", "update the seed",
    "write the seed", "let's update", "that's everything", "that's all",
    "done talking", "go ahead", "produce it", "make the changes",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_apply_signal(text: str) -> bool:
    low = text.lower().strip()
    return any(kw in low for kw in _APPLY_KEYWORDS)


def _next_backup_path(project_dir: Path) -> Path:
    n = 1
    while (project_dir / f"seed_v{n}.md").exists():
        n += 1
    return project_dir / f"seed_v{n}.md"


def _backup_and_save(pipeline, old_seed: str, new_seed: str) -> None:
    backup_path = _next_backup_path(pipeline.project_dir)
    pipeline.write_file(old_seed, backup_path.name)
    pipeline.write_file(new_seed, "seed.md")
    show_info(f"Previous seed backed up to {backup_path.name}")
    show_success("seed.md updated.")


# ── Phase 1: Analysis ─────────────────────────────────────────────────────────

def _do_regenerate(
    initial_prompt: str, brief: str, stage_cfg, system: str, pipeline
) -> tuple[list[dict], str]:
    """Regenerate the analysis, optionally guided by a brief."""
    if brief:
        prompt = (
            f"{initial_prompt}\n\n"
            f"⚠ AUTHOR GUIDANCE — PRIORITY INSTRUCTION:\n{brief}\n"
            f"You MUST incorporate this guidance. It overrides default section structure where needed."
        )
        show_info(f"Regenerating with guidance: '{brief}'…")
    else:
        prompt = initial_prompt
        show_info("Regenerating from scratch…")

    messages: list[dict] = [{"role": "user", "content": prompt}]
    with LLMClient(stage_cfg) as client:
        analysis = stream_response(client.stream(system, messages), title="Seed Analysis")
    messages.append({"role": "assistant", "content": analysis})
    pipeline.write_file(analysis, "seed_analysis.md")
    return messages, analysis


def _run_analysis(pipeline, state: ProjectState, seed_text: str, stage_cfg, system) -> str:
    """
    Generate seed analysis and run the conversational review loop.
    Returns the approved analysis text.
    """
    loop = ApprovalLoop(allow_skip=False)
    initial_prompt = pipeline.build_user_prompt(
        "stage0.txt",
        project_name=state.project_name,
        seed_content=seed_text,
    )

    show_info(f"Analysing seed with {stage_cfg.model} (temp={stage_cfg.temperature})…")
    messages: list[dict] = [{"role": "user", "content": initial_prompt}]
    with LLMClient(stage_cfg) as client:
        analysis = stream_response(client.stream(system, messages), title="Seed Analysis")
    messages.append({"role": "assistant", "content": analysis})
    pipeline.write_file(analysis, "seed_analysis.md")

    while True:
        action, user_text = loop.wait("Seed Analysis")

        if action == ApprovalAction.APPROVE:
            if len(messages) > 2:
                show_info("Consolidating into final analysis…")
                messages.append({"role": "user", "content": _CONSOLIDATE_PROMPT})
                with LLMClient(stage_cfg) as client:
                    analysis = stream_response(
                        client.stream(system, messages), title="Final Seed Analysis"
                    )
                messages.append({"role": "assistant", "content": analysis})
                pipeline.write_file(analysis, "seed_analysis.md")
                action2, text2 = loop.wait("Final Seed Analysis")
                if action2 == ApprovalAction.FEEDBACK:
                    messages.append({"role": "user", "content": text2})
                    with LLMClient(stage_cfg) as client:
                        reply = stream_response(
                            client.stream(system, messages), title="Evelyn", border_style="cyan"
                        )
                    messages.append({"role": "assistant", "content": reply})
                    analysis = reply
                    pipeline.write_file(analysis, "seed_analysis.md")
                    continue
                elif action2 == ApprovalAction.REGENERATE:
                    messages, analysis = _do_regenerate(initial_prompt, text2, stage_cfg, system, pipeline)
                    continue
            return analysis

        elif action == ApprovalAction.REGENERATE:
            messages, analysis = _do_regenerate(initial_prompt, user_text, stage_cfg, system, pipeline)

        elif action == ApprovalAction.FEEDBACK:
            # Step 1 — ask a clarifying question before responding
            messages.append({"role": "user", "content": user_text})
            messages.append({
                "role": "user",
                "content": "Before you respond to my feedback, ask me one focused clarifying question.",
            })
            with LLMClient(stage_cfg) as client:
                question = stream_response(
                    client.stream(system, messages), title="Evelyn", border_style="cyan"
                )
            messages.append({"role": "assistant", "content": question})

            # Step 2 — get the author's answer
            while True:
                answer = (questionary.text("You", style=_STYLE).ask() or "").strip()
                if answer:
                    break
            messages.append({"role": "user", "content": answer})

            # Step 3 — now produce the substantive response
            with LLMClient(stage_cfg) as client:
                reply = stream_response(
                    client.stream(system, messages), title="Evelyn", border_style="cyan"
                )
            messages.append({"role": "assistant", "content": reply})
            analysis = reply
            pipeline.write_file(analysis, "seed_analysis.md")

        elif action == ApprovalAction.EDIT:
            pipeline.write_file(user_text, "seed_analysis.md")
            return user_text


# ── Phase 2: Improvement session ──────────────────────────────────────────────

def _run_improvement(pipeline, seed_text: str, analysis_text: str, stage_cfg, system) -> str | None:
    """
    AI-led conversational improvement of the seed.
    Returns the proposed new seed text, or None if the author cancels.
    """
    console.print()
    console.print("[bold]Seed Improvement Session[/bold]")
    console.print(
        "[dim]The AI will ask focused questions to develop your premise. "
        "Type your thoughts freely. When ready to generate the updated seed, "
        "choose 'Generate updated seed' from the menu.[/dim]\n"
    )

    kickoff = pipeline.build_user_prompt(
        "stage0_improve.txt",
        seed_content=seed_text,
        analysis_content=analysis_text,
    )

    messages: list[dict] = [{"role": "user", "content": kickoff}]
    with LLMClient(stage_cfg) as client:
        reply = stream_response(
            client.stream(system, messages), title="Evelyn", border_style="cyan"
        )
    messages.append({"role": "assistant", "content": reply})

    while True:
        action = questionary.select(
            "Your move",
            choices=[
                questionary.Choice("Continue the conversation…", value="chat"),
                questionary.Choice("Generate updated seed now", value="apply"),
                questionary.Choice("Cancel — return without changes", value="cancel"),
            ],
            style=_STYLE,
        ).ask()

        if action is None or action == "cancel":
            console.print("[dim]Improvement session cancelled.[/dim]")
            return None

        if action == "apply":
            return _generate_proposed_seed(messages, "Let's write the updated seed now.", stage_cfg, system)

        # "chat" — get the user's message then continue
        raw = (questionary.text("You", style=_STYLE).ask() or "").strip()
        if not raw:
            continue
        messages.append({"role": "user", "content": raw})
        with LLMClient(stage_cfg) as client:
            reply = stream_response(
                client.stream(system, messages), title="Evelyn", border_style="cyan"
            )
        messages.append({"role": "assistant", "content": reply})


def _generate_proposed_seed(
    messages: list[dict], apply_signal: str, stage_cfg, system: str
) -> str | None:
    """
    Step 1: AI confirms agreed decisions.
    Step 2: Author confirms (or corrects).
    Step 3: AI writes the full updated seed.
    Returns new seed text, or None if author cancels at confirm step.
    """
    # Step 1 — list agreed changes
    messages.append({"role": "user", "content": apply_signal})
    messages.append({"role": "user", "content": _CONFIRM_DECISIONS_PROMPT})
    with LLMClient(stage_cfg) as client:
        decisions = stream_response(
            client.stream(system, messages),
            title="Agreed changes — confirm before writing",
            border_style="yellow",
        )
    messages.append({"role": "assistant", "content": decisions})

    # Step 2 — author confirms or corrects
    confirm_action = questionary.select(
        "Confirm these changes before writing?",
        choices=[
            questionary.Choice("Confirm — write the seed", value="confirm"),
            questionary.Choice("Make a correction first…",  value="correct"),
            questionary.Choice("Cancel — return to conversation", value="cancel"),
        ],
        style=_STYLE,
    ).ask()

    if confirm_action is None or confirm_action == "cancel":
        console.print("[dim]Seed generation cancelled. Returning to conversation.[/dim]")
        return None

    if confirm_action == "correct":
        correction = (questionary.text("Your correction", style=_STYLE).ask() or "").strip()
        if correction:
            messages.append({"role": "user", "content": f"One correction before we write: {correction}"})
            with LLMClient(stage_cfg) as client:
                ack = stream_response(
                    client.stream(system, messages), title="Evelyn", border_style="cyan"
                )
            messages.append({"role": "assistant", "content": ack})

    # Step 3 — write the seed
    show_info("Writing updated seed…")
    messages.append({"role": "user", "content": _GENERATE_SEED_PROMPT})
    with LLMClient(stage_cfg) as client:
        new_seed = stream_response(
            client.stream(system, messages), title="Proposed Updated Seed"
        )

    return new_seed


def _approve_proposed_seed(new_seed: str) -> str | None:
    """
    Ask for approval of the proposed seed (already displayed by stream_response).
    Returns the approved seed text (may be hand-edited), or None to redo.
    """
    action = questionary.select(
        "Accept this seed?",
        choices=[
            questionary.Choice("Approve — save this seed", value="approve"),
            questionary.Choice("Edit manually",             value="edit"),
            questionary.Choice("Redo — restart improvement", value="redo"),
        ],
        style=_STYLE,
    ).ask()

    if action is None or action == "redo":
        return None

    if action == "approve":
        return new_seed

    # edit
    console.print("[dim]Paste your edited seed. Enter a line containing only END when done.[/dim]")
    lines: list[str] = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


# ── Outstanding issues extraction ────────────────────────────────────────────

_ISSUES_PROMPT = """\
Compare the seed analysis below with the current seed document.
Identify every issue raised in the analysis that is still unresolved or only partially addressed.

SEED ANALYSIS:
---
[[analysis]]
---

CURRENT SEED:
---
[[seed]]
---

Output a numbered list. For each outstanding issue:
1. State the issue in one clear sentence.
2. On the same line, in parentheses, name the pipeline stage best placed to resolve it:
   (Story Bible / World Builder / Characters / Chapter Outlines / Chapter Writing)

Be specific and brief. If a issue was fully resolved by the seed revision, omit it.
If all issues were resolved, write exactly: All issues from the seed analysis have been resolved.
"""


def _extract_outstanding_issues(
    pipeline, seed_text: str, analysis: str, stage_cfg, system: str
) -> None:
    """
    Compare the final analysis against the current seed, extract unresolved
    issues, and save them to outstanding_issues.md for injection into later stages.
    """
    show_info("Extracting outstanding issues to carry forward…")
    prompt = _ISSUES_PROMPT.replace("[[analysis]]", analysis).replace("[[seed]]", seed_text)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    with LLMClient(stage_cfg) as client:
        issues = stream_response(
            client.stream(system, messages),
            title="Outstanding Issues",
            border_style="yellow",
        )
    pipeline.write_file(issues, "outstanding_issues.md")
    show_success("outstanding_issues.md saved — will be injected into all subsequent stages.")


# ── Stage entry point ─────────────────────────────────────────────────────────

def run(pipeline, state: ProjectState) -> ProjectState:
    """
    Stage 0 — iterative seed development loop:

        analyse → [improve → backup → save → re-analyse] × N → move to Stage 1
    """
    seed_text = pipeline.read_file("seed.md")
    if not seed_text.strip():
        raise StageError("seed.md is empty or missing. Add your premise before running Stage 0.")

    # Back up the original seed before touching anything
    if not list(pipeline.project_dir.glob("seed_v*.md")):
        backup = _next_backup_path(pipeline.project_dir)
        pipeline.write_file(seed_text, backup.name)
        show_info(f"Original seed backed up to {backup.name}")

    stage_cfg = resolve_stage(pipeline.cfg, "seed_analysis")
    system = pipeline.build_system_prompt("seed_analysis")

    while True:
        # ── Phase 1: Analyse current seed ────────────────────────────────────
        analysis = _run_analysis(pipeline, state, seed_text, stage_cfg, system)
        show_success("seed_analysis.md saved.")

        # ── Decision point: improve or move on ───────────────────────────────
        choice = questionary.select(
            "What next?",
            choices=[
                questionary.Choice("Approve — move to Story Bible", value="approve"),
                questionary.Choice("Improve the seed further",       value="improve"),
            ],
            style=_STYLE,
        ).ask()

        if choice == "approve" or choice is None:
            _extract_outstanding_issues(pipeline, seed_text, analysis, stage_cfg, system)
            state.current_stage = "stage1_bible"
            save_state(state, pipeline.project_dir)
            return state

        # ── Phase 2: Improvement → save → loop back to re-analyse ────────────
        while True:
            new_seed = _run_improvement(pipeline, seed_text, analysis, stage_cfg, system)

            if new_seed is None:
                # Cancelled — offer the choice again without re-analysing
                choice = questionary.select(
                    "What next?",
                    choices=[
                        questionary.Choice("Approve — move to Story Bible", value="approve"),
                        questionary.Choice("Try improvement again",          value="improve"),
                    ],
                    style=_STYLE,
                ).ask()
                if choice == "approve" or choice is None:
                    _extract_outstanding_issues(pipeline, seed_text, analysis, stage_cfg, system)
                    state.current_stage = "stage1_bible"
                    save_state(state, pipeline.project_dir)
                    return state
                continue

            approved_seed = _approve_proposed_seed(new_seed)

            if approved_seed is None:
                continue  # redo improvement

            # Approved — backup old, save new, update outstanding issues,
            # break → outer loop → re-analyse with fresh seed
            _backup_and_save(pipeline, seed_text, approved_seed)
            seed_text = approved_seed
            break

from __future__ import annotations

from enum import Enum, auto

import questionary
from questionary import Style
from rich.console import Console

console = Console()

# Questionary style that matches Rich's dim/cyan palette
_STYLE = Style([
    ("qmark",        "fg:#5fd7ff bold"),
    ("question",     "bold"),
    ("answer",       "fg:#5fd7ff bold"),
    ("pointer",      "fg:#5fd7ff bold"),
    ("highlighted",  "fg:#5fd7ff bold"),
    ("selected",     "fg:#afffff"),
    ("separator",    "fg:#555555"),
    ("instruction",  "fg:#555555"),
])


class ApprovalAction(Enum):
    APPROVE = auto()
    APPROVE_ALL = auto()
    REGENERATE = auto()
    FEEDBACK = auto()
    EDIT = auto()
    SKIP = auto()
    USE_REVIEW = auto()


class ApprovalLoop:
    """
    Presents a questionary selection menu and returns the chosen action.

    Parameters
    ----------
    allow_skip       : show a "Skip" option
    allow_approve_all: show an "Approve all remaining" option
    allow_use_review : show an "Apply review feedback" option
    """

    def __init__(
        self,
        allow_skip: bool = False,
        allow_approve_all: bool = False,
        allow_use_review: bool = False,
    ):
        self._allow_skip = allow_skip
        self._allow_approve_all = allow_approve_all
        self._allow_use_review = allow_use_review

    def wait(self, context: str = "") -> tuple[ApprovalAction, str]:
        """
        Show the action menu and collect any required follow-up text.
        Returns (action, text) where text is non-empty for REGENERATE (brief),
        FEEDBACK (message), and EDIT (pasted content).
        """
        choices = self._build_choices()

        print()  # blank line before the menu
        selected = questionary.select(
            context or "What would you like to do?",
            choices=choices,
            style=_STYLE,
            use_shortcuts=False,
        ).ask()

        if selected is None:
            # Ctrl-C / interrupted — treat as keyboard interrupt
            raise KeyboardInterrupt

        if selected == "_regenerate_guided":
            guidance = questionary.text(
                "Guidance for this regeneration:",
                style=_STYLE,
            ).ask()
            return ApprovalAction.REGENERATE, (guidance or "").strip()

        if selected == ApprovalAction.FEEDBACK:
            message = questionary.text(
                "Your feedback:",
                style=_STYLE,
            ).ask()
            return ApprovalAction.FEEDBACK, (message or "").strip()

        if selected == ApprovalAction.EDIT:
            console.print(
                "[dim]Paste your edited version below. "
                "Enter a line containing only [bold]END[/bold] when done.[/dim]"
            )
            lines: list[str] = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)
            return ApprovalAction.EDIT, "\n".join(lines)

        return selected, ""

    def _build_choices(self) -> list[questionary.Choice]:
        choices: list[questionary.Choice] = [
            questionary.Choice("Approve", value=ApprovalAction.APPROVE),
        ]
        if self._allow_approve_all:
            choices.append(
                questionary.Choice("Approve all remaining (auto)", value=ApprovalAction.APPROVE_ALL)
            )
        choices += [
            questionary.Choice("Regenerate  (fresh start)", value=ApprovalAction.REGENERATE),
            questionary.Choice("Regenerate with guidance…", value="_regenerate_guided"),
            questionary.Choice("Discuss / give feedback…",  value=ApprovalAction.FEEDBACK),
            questionary.Choice("Edit manually",              value=ApprovalAction.EDIT),
        ]
        if self._allow_use_review:
            choices.append(
                questionary.Choice("Apply AI review feedback", value=ApprovalAction.USE_REVIEW)
            )
        if self._allow_skip:
            choices.append(
                questionary.Choice("Skip", value=ApprovalAction.SKIP)
            )
        return choices

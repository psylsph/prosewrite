from __future__ import annotations

import re
from enum import Enum, auto

from rich.console import Console
from rich.prompt import Prompt

console = Console()


class ApprovalAction(Enum):
    APPROVE = auto()
    REGENERATE = auto()
    FEEDBACK = auto()
    EDIT = auto()
    SKIP = auto()
    USE_REVIEW = auto()


# Keywords that map to each action (checked in order, case-insensitive)
_PATTERNS: list[tuple[ApprovalAction, list[str]]] = [
    (ApprovalAction.APPROVE,     ["yes", "good", "looks great", "approve", "next", "perfect",
                                  "great", "ok", "okay", "done", "ship it", "lgtm"]),
    (ApprovalAction.REGENERATE,  ["redo", "again", "not right", "regenerate", "try again",
                                  "start over", "redo", "rewrite", "no good", "nope"]),
    (ApprovalAction.USE_REVIEW,  ["use review", "apply review", "use the review", "apply the review"]),
    (ApprovalAction.EDIT,        ["i'll edit", "ill edit", "editing myself", "i will edit",
                                  "let me edit", "i'll do it", "manual edit"]),
    (ApprovalAction.SKIP,        ["skip"]),
]


def _classify(text: str) -> tuple[ApprovalAction, str]:
    """Return (action, cleaned_text). Falls back to FEEDBACK with the raw text."""
    normalised = text.strip().lower()
    for action, keywords in _PATTERNS:
        for kw in keywords:
            if kw in normalised:
                return action, text.strip()
    # Anything else is treated as directed feedback
    return ApprovalAction.FEEDBACK, text.strip()


class ApprovalLoop:
    """
    Presents generated content to the user and waits for a decision.
    Reads free text and maps it to an ApprovalAction without re-printing options.
    """

    def __init__(self, allow_skip: bool = False):
        self._allow_skip = allow_skip

    def wait(self, prompt_hint: str = "") -> tuple[ApprovalAction, str]:
        """
        Block until the user enters a decision. Returns (action, user_text).
        user_text is the raw input — useful when action is FEEDBACK or EDIT.
        """
        hint = prompt_hint or "Your call"
        while True:
            raw = Prompt.ask(f"\n[bold cyan]{hint}[/bold cyan]")
            if not raw.strip():
                continue

            action, text = _classify(raw)

            if action == ApprovalAction.SKIP and not self._allow_skip:
                console.print("[dim]Skip is not available at this stage.[/dim]")
                continue

            if action == ApprovalAction.EDIT:
                console.print(
                    "[dim]Paste your edited version below. "
                    "Enter a line with just [bold]END[/bold] when done.[/dim]"
                )
                lines: list[str] = []
                while True:
                    line = input()
                    if line.strip() == "END":
                        break
                    lines.append(line)
                return ApprovalAction.EDIT, "\n".join(lines)

            return action, text

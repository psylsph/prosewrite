from __future__ import annotations

import re
from enum import Enum, auto

from rich.console import Console
from rich.prompt import Prompt

console = Console()


class ApprovalAction(Enum):
    APPROVE = auto()
    APPROVE_ALL = auto()
    REGENERATE = auto()
    FEEDBACK = auto()
    EDIT = auto()
    SKIP = auto()
    USE_REVIEW = auto()


# Keywords that map to each action (checked in order, case-insensitive)
_PATTERNS: list[tuple[ApprovalAction, list[str]]] = [
    (ApprovalAction.APPROVE_ALL, ["approve all", "approve remaining", "auto approve",
                                  "auto-approve", "approve rest"]),
    (ApprovalAction.APPROVE,     ["yes", "good", "looks great", "approve", "next", "perfect",
                                  "great", "ok", "okay", "done", "ship it", "lgtm"]),
    (ApprovalAction.REGENERATE,  ["redo", "again", "not right", "regenerate", "try again",
                                  "start over", "redo", "rewrite", "no good", "nope"]),
    (ApprovalAction.USE_REVIEW,  ["use review", "apply review", "use the review", "apply the review"]),
    (ApprovalAction.EDIT,        ["i'll edit", "ill edit", "editing myself", "i will edit",
                                  "let me edit", "i'll do it", "manual edit"]),
    (ApprovalAction.SKIP,        ["skip"]),
]


# Words that don't count as "substantive content" after an action keyword
_FILLER = {"please", "it", "this", "that", "the", "a", "an", "now", "just"}


def _classify(text: str) -> tuple[ApprovalAction, str]:
    """
    Return (action, cleaned_text). Falls back to FEEDBACK with the raw text.

    A keyword only acts as a command when it appears at (or very near) the
    start of the input — i.e. nothing substantive precedes it.  This prevents
    words like "good" or "ok" embedded in a guidance sentence from being
    mistakenly treated as APPROVE.

    - REGENERATE + instruction → still REGENERATE, text carries the brief
      e.g. "regenerate add more weapon stashes" → (REGENERATE, "add more weapon stashes")

    - Other single-word keywords + trailing instruction → FEEDBACK
      e.g. the keyword was incidental; treat whole message as directed notes.

    Phrase keywords (e.g. "not right", "try again") are matched as-is.
    """
    normalised = text.strip().lower()
    for action, keywords in _PATTERNS:
        for kw in keywords:
            if kw not in normalised:
                continue

            idx = normalised.index(kw)

            # Reject if substantial content precedes the keyword — it means the
            # keyword is embedded mid-sentence (e.g. "...would be good"), not a command.
            before = normalised[:idx].strip()
            before_words = [w for w in before.split() if w not in _FILLER]
            if before_words:
                continue

            after = normalised[idx + len(kw):].strip()
            after_words = [w for w in after.split() if w not in _FILLER]
            is_single_word = ' ' not in kw

            if is_single_word and after_words:
                # Strip leading punctuation (e.g. the colon in "regenerate: note")
                instruction = text.strip()[idx + len(kw):].strip().lstrip(":").strip()
                if action == ApprovalAction.REGENERATE:
                    return ApprovalAction.REGENERATE, instruction
                else:
                    return ApprovalAction.FEEDBACK, instruction or text.strip()

            # Bare keyword with no trailing instruction — return empty string so
            # stages don't mistake the keyword itself (e.g. "regenerate") for a brief.
            return action, ""

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

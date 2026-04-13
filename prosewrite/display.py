from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()


@dataclass
class ReviewResult:
    score: float
    summary: str
    full_text: str
    revision_brief: str = ""


def show_stage_header(stage_name: str, stage_num: int) -> None:
    console.print()
    console.print(Rule(f"[bold]Stage {stage_num} — {stage_name}[/bold]", style="blue"))
    console.print()


def show_draft(text: str, title: str = "Draft", word_count: int | None = None) -> None:
    subtitle = f"{word_count:,} words" if word_count is not None else ""
    console.print(
        Panel(
            Markdown(text),
            title=f"[bold]{title}[/bold]",
            subtitle=subtitle,
            border_style="dim",
            padding=(1, 2),
        )
    )


def show_review(review: ReviewResult) -> None:
    score_colour = "green" if review.score >= 7 else "yellow" if review.score >= 5 else "red"
    console.print(
        Panel(
            Markdown(review.full_text),
            title=f"[bold]AI Review[/bold]  [bold {score_colour}]{review.score}/10[/bold {score_colour}]",
            border_style=score_colour,
            padding=(1, 2),
        )
    )


def show_warning(message: str) -> None:
    console.print(
        Panel(
            Text(message, style="yellow"),
            border_style="yellow",
            title="[yellow bold]Warning[/yellow bold]",
        )
    )


def show_success(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def show_error(message: str) -> None:
    console.print(f"[red]✗[/red] {message}")


def show_info(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def word_count(text: str) -> int:
    return len(text.split())

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from rich.console import Console
from rich.live import Live
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


_STREAM_TAIL_LINES = 30  # lines shown during streaming (tail scroll)


def stream_response(
    chunks: Iterator[str],
    title: str = "Response",
    border_style: str = "dim",
) -> str:
    """
    Stream LLM output into a Live panel.

    During streaming: shows the last _STREAM_TAIL_LINES lines so the panel
    never overflows the terminal, with a live word count in the subtitle.

    On completion: renders the full text as Markdown (scrollable in terminal
    history) with a final word count.
    """
    full_text = ""

    def _streaming_panel(text: str) -> Panel:
        wc = word_count(text)
        lines = text.splitlines()
        visible = "\n".join(lines[-_STREAM_TAIL_LINES:]) if len(lines) > _STREAM_TAIL_LINES else text
        return Panel(
            Text(visible + "▌"),
            title=f"[bold]{title}[/bold]",
            subtitle=f"[dim]{wc:,} words…[/dim]",
            border_style=border_style,
            padding=(1, 2),
        )

    def _done_panel(text: str) -> Panel:
        return Panel(
            Markdown(text),
            title=f"[bold]{title}[/bold]",
            subtitle=f"{word_count(text):,} words",
            border_style=border_style,
            padding=(1, 2),
        )

    with Live(_streaming_panel(""), refresh_per_second=15, console=console) as live:
        for chunk in chunks:
            full_text += chunk
            live.update(_streaming_panel(full_text))
        live.update(_done_panel(full_text))

    return full_text


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

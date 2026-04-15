from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import load_config, validate_config, DEFAULT_CONFIG_PATH
from .exceptions import ConfigError, StateError
from .state import load_state, new_state, save_state

app = typer.Typer(
    name="prosewrite",
    help="AI book writing pipeline — multi-stage, human-in-the-loop.",
    no_args_is_help=True,
)
console = Console()

_DEFAULT_CONFIG = typer.Option(
    str(DEFAULT_CONFIG_PATH), "--config", help="Path to config.toml"
)


def _get_project_dir(config_path: Path, project_name: str) -> Path:
    cfg = load_config(config_path)
    base = Path(cfg.output_dir)
    return base / project_name


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


@app.command()
def new(
    name: str = typer.Option(
        ..., "--name", help="Project name (used as directory name)."
    ),
    seed: Path = typer.Option(..., "--seed", help="Path to seed.md file."),
    config: Path = _DEFAULT_CONFIG,
) -> None:
    """Start a new writing project."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)

    project_dir = Path(cfg.output_dir) / name
    if project_dir.exists():
        console.print(
            f"[yellow]Project '{name}' already exists at {project_dir}.[/yellow]"
        )
        console.print("Use [bold]prosewrite resume[/bold] to continue it.")
        raise typer.Exit(1)

    if not seed.exists():
        console.print(f"[red]Seed file not found:[/red] {seed}")
        raise typer.Exit(1)

    # Create project directory structure
    for subdir in ("characters", "chapter_outlines", "chapters", "summaries"):
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    shutil.copy(seed, project_dir / "seed.md")

    state = new_state(
        name,
        config_style={
            "pov": cfg.style.pov,
            "tense": cfg.style.tense,
            "genre": cfg.style.genre,
            "min_words": cfg.style.min_words,
        },
    )
    save_state(state, project_dir)

    console.print(f"[green]Created project '{name}'[/green] at {project_dir}")
    console.print("Starting pipeline…\n")

    from .pipeline import Pipeline

    pipeline = Pipeline(cfg, project_dir)
    pipeline.run(state)


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@app.command()
def resume(
    name: str = typer.Option(..., "--name", help="Project name to resume."),
    config: Path = _DEFAULT_CONFIG,
) -> None:
    """Resume an existing project from where it left off."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)

    project_dir = Path(cfg.output_dir) / name
    try:
        state = load_state(project_dir)
    except StateError as e:
        console.print(f"[red]State error:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        f"[green]Resuming '{name}'[/green] at stage [bold]{state.current_stage}[/bold]"
    )

    from .pipeline import Pipeline

    pipeline = Pipeline(cfg, project_dir)
    pipeline.run(state)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command()
def run(
    name: str = typer.Option(..., "--name", help="Project name."),
    stage: str = typer.Option(
        ..., "--stage", help="Stage name to run (e.g. characters)."
    ),
    chapter: int = typer.Option(
        None,
        "--chapter",
        help="Chapter number to regenerate (only for 'chapters' stage).",
    ),
    config: Path = _DEFAULT_CONFIG,
) -> None:
    """Run a specific stage for a project (useful for reruns)."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)

    project_dir = Path(cfg.output_dir) / name
    try:
        state = load_state(project_dir)
    except StateError as e:
        console.print(f"[red]State error:[/red] {e}")
        raise typer.Exit(1)

    from .pipeline import Pipeline, STAGE_ORDER

    if stage not in STAGE_ORDER:
        console.print(f"[red]Unknown stage:[/red] {stage}")
        console.print(f"Valid stages: {', '.join(STAGE_ORDER)}")
        raise typer.Exit(1)

    # Handle chapter-specific regeneration
    if chapter is not None:
        if stage != "chapters":
            console.print(
                f"[red]Error:[/red] --chapter flag can only be used with --stage chapters"
            )
            raise typer.Exit(1)
        if chapter not in state.progress.approved_chapters:
            console.print(
                f"[yellow]Chapter {chapter} is not in approved chapters.[/yellow]"
            )
            console.print(
                f"[dim]It will be generated when you run the chapters stage normally.[/dim]"
            )
            raise typer.Exit(0)

        # Remove chapter from approved list to force regeneration
        state.progress.approved_chapters.remove(chapter)
        from .state import save_state

        save_state(state, project_dir)
        console.print(f"[green]✓ Chapter {chapter} removed from approved list.[/green]")
        console.print(
            f"[dim]It will be regenerated when the chapters stage runs.[/dim]"
        )

    state.current_stage = stage
    pipeline = Pipeline(cfg, project_dir)
    pipeline.run(state)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_projects(
    config: Path = _DEFAULT_CONFIG,
) -> None:
    """List all projects and their current pipeline stage."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)

    output_dir = Path(cfg.output_dir)
    if not output_dir.exists():
        console.print(
            f"[dim]No projects found (output_dir '{output_dir}' does not exist).[/dim]"
        )
        return

    projects = sorted(output_dir.iterdir())
    if not projects:
        console.print("[dim]No projects found.[/dim]")
        return

    table = Table(title="Prosewrite Projects", show_lines=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Stage")
    table.add_column("Chapters approved")
    table.add_column("Notes")

    for p in projects:
        if not p.is_dir():
            continue
        try:
            state = load_state(p)
            chapters = f"{len(state.progress.approved_chapters)} / {state.settings.total_chapters or '?'}"
            table.add_row(
                state.project_name, state.current_stage, chapters, state.notes[:60]
            )
        except StateError:
            table.add_row(p.name, "[red]no state file[/red]", "-", "")

    console.print(table)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@app.command()
def export(
    name: str = typer.Option(..., "--name", help="Project name to export."),
    config: Path = _DEFAULT_CONFIG,
) -> None:
    """Export approved chapters to manuscript.md (and optionally .docx)."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)

    project_dir = Path(cfg.output_dir) / name
    try:
        state = load_state(project_dir)
    except StateError as e:
        console.print(f"[red]State error:[/red] {e}")
        raise typer.Exit(1)

    from .stages.stage6_export import run as run_export

    run_export(cfg, project_dir, state)


# ---------------------------------------------------------------------------
# config check
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="Config file utilities.")
app.add_typer(config_app, name="config")


@config_app.command(name="check")
def config_check(
    config: Path = _DEFAULT_CONFIG,
) -> None:
    """Validate the config file and report any issues."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)

    warnings = validate_config(cfg)
    if not warnings:
        console.print(f"[green]Config OK[/green] ({config})")
    else:
        console.print(f"[yellow]Config warnings ({len(warnings)}):[/yellow]")
        for w in warnings:
            console.print(f"  [yellow]•[/yellow] {w}")

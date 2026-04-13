from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .exceptions import StateError

STATE_FILENAME = "project_state.json"


@dataclass
class ProjectSettings:
    pov: str = "third person limited"
    tense: str = "past"
    genre: str = ""
    min_words_per_chapter: int = 3000
    total_chapters: int = 0


@dataclass
class ProjectProgress:
    approved_outlines: list[int] = field(default_factory=list)
    approved_chapters: list[int] = field(default_factory=list)
    last_approved_chapter: int = 0


@dataclass
class ProjectState:
    project_name: str
    current_stage: str = "stage0_seed"
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    progress: ProjectProgress = field(default_factory=ProjectProgress)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "current_stage": self.current_stage,
            "settings": asdict(self.settings),
            "progress": asdict(self.progress),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectState":
        settings_data = data.get("settings", {})
        progress_data = data.get("progress", {})
        return cls(
            project_name=data["project_name"],
            current_stage=data.get("current_stage", "stage0_seed"),
            settings=ProjectSettings(**settings_data),
            progress=ProjectProgress(**progress_data),
            notes=data.get("notes", ""),
        )


def load_state(project_dir: Path) -> ProjectState:
    state_path = project_dir / STATE_FILENAME
    if not state_path.exists():
        raise StateError(f"State file not found: {state_path}")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ProjectState.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise StateError(f"Failed to parse state file {state_path}: {e}") from e


def save_state(state: ProjectState, project_dir: Path) -> None:
    state_path = project_dir / STATE_FILENAME
    tmp_path = state_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)
        os.replace(tmp_path, state_path)  # atomic on POSIX
    except OSError as e:
        raise StateError(f"Failed to save state file {state_path}: {e}") from e
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def new_state(project_name: str, config_style: dict | None = None) -> ProjectState:
    """Create a fresh ProjectState for a new project, optionally seeding style from config."""
    settings = ProjectSettings()
    if config_style:
        settings.pov = config_style.get("pov", settings.pov)
        settings.tense = config_style.get("tense", settings.tense)
        settings.genre = config_style.get("genre", settings.genre)
        settings.min_words_per_chapter = config_style.get("min_words", settings.min_words_per_chapter)
    return ProjectState(project_name=project_name, settings=settings)

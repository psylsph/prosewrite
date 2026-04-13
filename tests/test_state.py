import json
from pathlib import Path

import pytest

from prosewrite.state import (
    ProjectProgress,
    ProjectSettings,
    ProjectState,
    load_state,
    new_state,
    save_state,
)
from prosewrite.exceptions import StateError


class TestProjectState:
    def test_to_dict_round_trips(self):
        state = ProjectState(
            project_name="test_novel",
            current_stage="stage2_world",
            settings=ProjectSettings(genre="thriller", total_chapters=24),
            progress=ProjectProgress(approved_chapters=[1, 2, 3], last_approved_chapter=3),
            notes="Some notes",
        )
        d = state.to_dict()
        restored = ProjectState.from_dict(d)
        assert restored.project_name == state.project_name
        assert restored.current_stage == state.current_stage
        assert restored.settings.total_chapters == 24
        assert restored.progress.approved_chapters == [1, 2, 3]
        assert restored.notes == "Some notes"

    def test_from_dict_uses_defaults_for_missing_keys(self):
        minimal = {"project_name": "minimal", "settings": {}, "progress": {}}
        state = ProjectState.from_dict(minimal)
        assert state.current_stage == "stage0_seed"
        assert state.settings.min_words_per_chapter == 3000
        assert state.progress.approved_chapters == []


class TestSaveLoadState:
    def test_save_and_load_round_trips(self, tmp_path):
        state = ProjectState(
            project_name="round_trip",
            current_stage="stage3_characters",
            settings=ProjectSettings(genre="sci-fi", total_chapters=18),
            progress=ProjectProgress(approved_outlines=[1, 2], approved_chapters=[1]),
        )
        save_state(state, tmp_path)
        loaded = load_state(tmp_path)
        assert loaded.project_name == "round_trip"
        assert loaded.settings.genre == "sci-fi"
        assert loaded.settings.total_chapters == 18
        assert loaded.progress.approved_outlines == [1, 2]

    def test_save_is_atomic(self, tmp_path):
        """Ensure no .tmp file is left behind after a successful save."""
        state = new_state("atomic_test")
        save_state(state, tmp_path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(StateError, match="not found"):
            load_state(tmp_path)

    def test_load_corrupt_json_raises(self, tmp_path):
        (tmp_path / "project_state.json").write_text("not json {{{", encoding="utf-8")
        with pytest.raises(StateError):
            load_state(tmp_path)


class TestNewState:
    def test_new_state_defaults(self):
        state = new_state("my_book")
        assert state.project_name == "my_book"
        assert state.current_stage == "stage0_seed"

    def test_new_state_applies_style(self):
        state = new_state("styled", config_style={"pov": "first person", "genre": "horror", "min_words": 4000})
        assert state.settings.pov == "first person"
        assert state.settings.genre == "horror"
        assert state.settings.min_words_per_chapter == 4000

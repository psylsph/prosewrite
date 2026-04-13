import os
import textwrap
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from prosewrite.config import (
    StageSettings,
    _merge_stage,
    _parse_defaults,
    load_config,
    resolve_stage,
    validate_config,
    DEFAULT_CONFIG_PATH,
)


def _make_defaults(**overrides) -> StageSettings:
    base = dict(
        api_base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        model="claude-opus-4-5",
        temperature=0.7,
        max_tokens=4096,
        timeout_s=120,
    )
    base.update(overrides)
    return StageSettings(**base)


class TestMergeStage:
    def test_no_overrides_returns_defaults(self):
        defaults = _make_defaults()
        result = _merge_stage(defaults, {})
        assert result.model == defaults.model
        assert result.temperature == defaults.temperature
        assert result.max_tokens == defaults.max_tokens
        assert result.api_base_url == defaults.api_base_url

    def test_partial_override(self):
        defaults = _make_defaults()
        result = _merge_stage(defaults, {"temperature": 0.2, "max_tokens": 1000})
        assert result.temperature == 0.2
        assert result.max_tokens == 1000
        assert result.model == defaults.model  # untouched

    def test_full_override(self):
        defaults = _make_defaults()
        overrides = dict(
            api_base_url="http://localhost:11434/v1",
            api_key_env="OLLAMA_KEY",
            model="llama3:70b",
            temperature=0.9,
            max_tokens=8000,
            timeout_s=60,
        )
        result = _merge_stage(defaults, overrides)
        assert result.api_base_url == "http://localhost:11434/v1"
        assert result.model == "llama3:70b"
        assert result.temperature == 0.9

    def test_temperature_is_float(self):
        defaults = _make_defaults()
        result = _merge_stage(defaults, {"temperature": "0.5"})
        assert isinstance(result.temperature, float)

    def test_max_tokens_is_int(self):
        defaults = _make_defaults()
        result = _merge_stage(defaults, {"max_tokens": "2000"})
        assert isinstance(result.max_tokens, int)


class TestLoadConfig:
    def test_loads_default_config(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        assert cfg.name  # project name set
        assert cfg.defaults.model
        assert cfg.persona.name
        assert len(cfg.stages) > 0

    def test_stage_inherits_defaults(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        # seed_analysis overrides temperature but not model
        stage = cfg.stages["seed_analysis"]
        assert stage.temperature == 1.0
        assert stage.model == cfg.defaults.model  # inherited

    def test_missing_config_raises(self, tmp_path):
        from prosewrite.exceptions import ConfigError
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.toml")

    def test_invalid_toml_raises(self, tmp_path):
        from prosewrite.exceptions import ConfigError
        bad = tmp_path / "bad.toml"
        bad.write_text("this is not [ valid toml !!!", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(bad)


class TestResolveStage:
    def test_known_stage(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        stage = resolve_stage(cfg, "seed_analysis")
        assert stage.temperature == 1.0

    def test_unknown_stage_falls_back_to_defaults(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        stage = resolve_stage(cfg, "nonexistent_stage")
        assert stage.model == cfg.defaults.model


class TestApiKey:
    def test_api_key_resolved_from_env(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        key_env = cfg.defaults.api_key_env
        with patch.dict(os.environ, {key_env: "test-key-123"}):
            assert cfg.defaults.api_key == "test-key-123"

    def test_missing_api_key_raises(self):
        from prosewrite.exceptions import ConfigError
        cfg = load_config(DEFAULT_CONFIG_PATH)
        key_env = cfg.defaults.api_key_env
        env = {k: v for k, v in os.environ.items() if k != key_env}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match=key_env):
                _ = cfg.defaults.api_key


class TestValidateConfig:
    def test_valid_config_returns_no_warnings(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        warnings = validate_config(cfg)
        assert warnings == []

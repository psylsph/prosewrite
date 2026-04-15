from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ConfigError

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


@dataclass
class StageSettings:
    api_base_url: str
    api_key_env: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: int

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ConfigError(
                f"API key environment variable '{self.api_key_env}' is not set. "
                f"Run: export {self.api_key_env}=<your-key>"
            )
        return key


@dataclass
class PersonaConfig:
    name: str
    description: str


@dataclass
class StyleConfig:
    pov: str
    tense: str
    min_words: int
    genre: str
    notes: str


@dataclass
class ProjectConfig:
    name: str
    author: str
    output_dir: str
    defaults: StageSettings
    stages: dict[str, StageSettings]
    persona: PersonaConfig
    style: StyleConfig
    prompts_dir: Path


def _parse_defaults(raw: dict) -> StageSettings:
    d = raw.get("defaults", {})
    return StageSettings(
        api_base_url=d.get("api_base_url", "https://api.anthropic.com/v1"),
        api_key_env=d.get("api_key_env", "ANTHROPIC_API_KEY"),
        model=d.get("model", "claude-opus-4-5"),
        temperature=float(d.get("temperature", 0.7)),
        max_tokens=int(d.get("max_tokens", 4096)),
        timeout_s=int(d.get("timeout_s", 120)),
    )


def _merge_stage(defaults: StageSettings, overrides: dict) -> StageSettings:
    return StageSettings(
        api_base_url=overrides.get("api_base_url", defaults.api_base_url),
        api_key_env=overrides.get("api_key_env", defaults.api_key_env),
        model=overrides.get("model", defaults.model),
        temperature=float(overrides.get("temperature", defaults.temperature)),
        max_tokens=int(overrides.get("max_tokens", defaults.max_tokens)),
        timeout_s=int(overrides.get("timeout_s", defaults.timeout_s)),
    )


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ProjectConfig:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Failed to parse config file {path}: {e}") from e

    defaults = _parse_defaults(raw)
    raw_stages = raw.get("stages", {})
    stages = {
        name: _merge_stage(defaults, overrides)
        for name, overrides in raw_stages.items()
    }

    raw_persona = raw.get("persona", {})
    persona = PersonaConfig(
        name=raw_persona.get("name", ""),
        description=raw_persona.get("description", "").strip(),
    )

    raw_style = raw.get("style", {})
    style = StyleConfig(
        pov=raw_style.get("pov", "third person limited"),
        tense=raw_style.get("tense", "past"),
        min_words=int(raw_style.get("min_words", 3000)),
        genre=raw_style.get("genre", ""),
        notes=raw_style.get("notes", "").strip(),
    )

    raw_project = raw.get("project", {})
    prompts_dir = Path(__file__).parent / "prompts"

    return ProjectConfig(
        name=raw_project.get("name", "my_novel"),
        author=raw_project.get("author", ""),
        output_dir=raw_project.get("output_dir", "projects"),
        defaults=defaults,
        stages=stages,
        persona=persona,
        style=style,
        prompts_dir=prompts_dir,
    )


def resolve_stage(cfg: ProjectConfig, stage_name: str) -> StageSettings:
    """Return the resolved StageSettings for a named stage, falling back to defaults."""
    return cfg.stages.get(stage_name, cfg.defaults)


def validate_config(cfg: ProjectConfig) -> list[str]:
    """Return a list of human-readable warnings about the config. Empty = valid."""
    warnings: list[str] = []

    if not cfg.persona.name:
        warnings.append(
            "[persona] name is empty — persona will not be injected into prompts."
        )
    if not cfg.persona.description:
        warnings.append(
            "[persona] description is empty — AI will use no editorial personality."
        )
    if cfg.style.min_words < 500:
        warnings.append(
            f"[style] min_words={cfg.style.min_words} seems very low for a chapter."
        )
    if cfg.defaults.temperature > 1.0:
        warnings.append(
            f"[defaults] temperature={cfg.defaults.temperature} may be out of range (max 1.0)."
        )

    for stage_name, stage in cfg.stages.items():
        if stage.temperature > 1.0:
            warnings.append(
                f"[stages.{stage_name}] temperature={stage.temperature} may be out of range (max 1.0)."
            )
        if stage.max_tokens < 256:
            warnings.append(
                f"[stages.{stage_name}] max_tokens={stage.max_tokens} is very low."
            )

    return warnings

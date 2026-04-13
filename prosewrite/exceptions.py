class ProsewriteError(Exception):
    """Base exception for all Prosewrite errors."""


class ConfigError(ProsewriteError):
    """Raised when configuration is invalid or missing."""


class LLMError(ProsewriteError):
    """Raised when an LLM API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMTimeoutError(LLMError):
    """Raised when an LLM API call times out."""


class StateError(ProsewriteError):
    """Raised when project state is invalid or unreadable."""


class StageError(ProsewriteError):
    """Raised when a pipeline stage fails."""


class PromptError(ProsewriteError):
    """Raised when a prompt file is missing or invalid."""

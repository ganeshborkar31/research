"""Provider-neutral embedding configuration and failure types."""

from __future__ import annotations

from dataclasses import dataclass


class EmbeddingError(RuntimeError):
    """Base class for provider and vector validation errors."""


class PermanentEmbeddingError(EmbeddingError):
    """A configuration, request, or response error that retries cannot fix."""


class TransientEmbeddingError(EmbeddingError):
    """A temporary provider error that may succeed when retried."""


class InputEmbeddingError(PermanentEmbeddingError):
    """A provider rejected one or more input texts in a batch."""


class ProviderSelectionError(PermanentEmbeddingError):
    """The configured provider is unknown or cannot be initialized."""


class EmbeddingValidationError(PermanentEmbeddingError):
    """The provider returned malformed or dimensionally incompatible vectors."""


class PartialEmbeddingError(EmbeddingError):
    """A batch contained failures; no partial vector list is returned."""

    def __init__(self, failed_indices: tuple[int, ...], message: str = "Some inputs could not be embedded") -> None:
        self.failed_indices = failed_indices
        super().__init__(f"{message}; failed input indices: {', '.join(map(str, failed_indices))}")


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str = "openai"
    model: str | None = None
    dimensions: int | None = None
    batch_size: int = 64
    max_retries: int = 3
    retry_backoff_seconds: float = 0.25
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("embedding provider must be configured")
        if self.dimensions is not None and self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        if self.batch_size <= 0:
            raise ValueError("embedding batch_size must be positive")
        if self.max_retries < 0 or self.retry_backoff_seconds < 0 or self.timeout_seconds <= 0:
            raise ValueError("retry and timeout settings are invalid")

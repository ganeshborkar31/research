"""Errors raised by document indexing and version replacement."""


class DocumentIndexingError(RuntimeError):
    """Base class for indexing failures."""


class IndexCollectionError(DocumentIndexingError):
    """The existing Qdrant collection is missing or incompatible."""


class PartialIndexingError(DocumentIndexingError):
    """A batch failed after some points were written; the prior version is retained."""

    def __init__(self, completed_points: int, total_points: int) -> None:
        self.completed_points = completed_points
        self.total_points = total_points
        super().__init__(f"Indexing failed after {completed_points} of {total_points} points")


class DocumentVersionConflict(DocumentIndexingError):
    """An active document version is immutable; changed content needs a new version ID."""


class StaleChunkCleanupError(DocumentIndexingError):
    """A new version is active but stale points could not all be removed."""


class DocumentIndexStateError(DocumentIndexingError):
    """Qdrant could not safely change the active version state."""

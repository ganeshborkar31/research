"""Shared document loader contracts."""

from pathlib import Path
from typing import Protocol

from app.rag.models.document import NormalizedDocument


class DocumentLoader(Protocol):
    def load(self, source: str | Path) -> NormalizedDocument:
        """Parse one local document into the application representation."""

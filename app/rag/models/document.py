"""Application-owned normalized document types used by ingestion stages."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DocumentElement:
    """A structural unit in source reading order."""

    kind: str
    text: str = ""
    page_number: int | None = None
    level: int | None = None
    caption: str | None = None
    image_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """Parsed source content independent of any parser implementation."""

    text: str
    elements: tuple[DocumentElement, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

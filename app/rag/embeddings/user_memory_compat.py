"""The deterministic vector function used by existing user-memory retrieval."""

from __future__ import annotations

from hashlib import sha256
from math import sqrt
from random import Random


def embed_user_memory_text(text: str, dimensions: int = 256) -> list[float]:
    """Generate the exact vector shape used by legacy user-memory query embeddings."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("embedding text must be non-empty")
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    seed = int.from_bytes(sha256(text.encode("utf-8")).digest()[:8], "big")
    rand = Random(seed)
    vector = [rand.uniform(-1.0, 1.0) for _ in range(dimensions)]
    magnitude = sqrt(sum(value * value for value in vector)) or 1.0
    return [value / magnitude for value in vector]

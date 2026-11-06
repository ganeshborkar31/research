"""Lexical retrieval boundary for the existing Qdrant index.

The current ingestion path stores text in payloads alongside one unnamed dense
vector. It does not configure a sparse vector or a Qdrant full-text payload index,
so there is no compatible lexical query operation yet. In particular, this module
must not invent a tokenization/ranking scheme or label dense search as lexical.
"""

from __future__ import annotations

from app.rag.models.retrieval import RetrievalQueryContext, RetrievalResult, RetrievalStrategyName


class LexicalRetrievalError(RuntimeError):
    """A lexical retrieval operation failed."""


class LexicalRetrievalConfigurationError(LexicalRetrievalError):
    """The existing index has no lexical query configuration."""


class LexicalRetriever:
    """Reject lexical requests until ingestion exposes a compatible lexical index.

    Existing indexing stores searchable content as payload text but only provisions
    an unnamed dense vector. Without a sparse vector or indexed full-text payload,
    Qdrant cannot execute lexical queries against the current collection.
    """

    async def retrieve(self, context: RetrievalQueryContext) -> list[RetrievalResult]:
        if context.strategy != RetrievalStrategyName.LEXICAL:
            raise ValueError("LexicalRetriever requires the lexical retrieval strategy")
        if not context.query.strip() or not context.active_version_ids:
            return []
        raise LexicalRetrievalConfigurationError(
            "Lexical retrieval is unavailable: the configured Qdrant collection has "
            "no compatible lexical index or query configuration"
        )

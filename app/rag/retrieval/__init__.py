"""Query preparation and retrieval strategy contracts."""

from app.rag.retrieval.query_processor import (
    QueryProcessingConfig,
    RetrievalAccessDenied,
    RetrievalQueryError,
    RetrievalQueryProcessor,
    RetrievalRequestPreparer,
    RetrievalScopeResolver,
)
from app.rag.retrieval.lexical import (
    LexicalRetrievalConfigurationError,
    LexicalRetrievalError,
    LexicalRetriever,
)
from app.rag.retrieval.fusion import FusionStrategy, ResultFusion
from app.rag.retrieval.hybrid import (
    HybridRetrievalConfig,
    HybridRetrievalError,
    HybridRetriever,
)
from app.rag.retrieval.reranking import (
    CandidateReranker,
    RerankerRegistry,
    RerankingConfig,
    RerankingError,
)

__all__ = [
    "QueryProcessingConfig",
    "RetrievalAccessDenied",
    "RetrievalQueryError",
    "RetrievalQueryProcessor",
    "RetrievalRequestPreparer",
    "RetrievalScopeResolver",
    "LexicalRetrievalConfigurationError",
    "LexicalRetrievalError",
    "LexicalRetriever",
    "FusionStrategy",
    "ResultFusion",
    "HybridRetrievalConfig",
    "HybridRetrievalError",
    "HybridRetriever",
    "CandidateReranker",
    "RerankerRegistry",
    "RerankingConfig",
    "RerankingError",
]


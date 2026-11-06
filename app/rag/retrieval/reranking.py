"""Provider selected, ownership preserving candidate reranking."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.models.retrieval import RetrievalResult
from app.rag.ports.reranker import RerankerPort


class RerankingError(RuntimeError):
    """A configured reranker failed or returned invalid candidates."""


@dataclass(frozen=True)
class RerankingConfig:
    enabled: bool = False
    provider: str | None = None
    top_k: int = 10


class RerankerRegistry:
    def __init__(self, providers: dict[str, RerankerPort] | None = None) -> None:
        self.providers = providers or {}

    def get(self, name: str) -> RerankerPort:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise RerankingError(f"Reranker provider {name!r} is not configured") from exc


class CandidateReranker:
    def __init__(self, config: RerankingConfig | None = None,
                 registry: RerankerRegistry | None = None) -> None:
        self.config = config or RerankingConfig()
        self.registry = registry or RerankerRegistry()

    async def rerank(self, query: str, candidates: Sequence[RetrievalResult]) -> list[RetrievalResult]:
        if not self.config.enabled or not candidates:
            return list(candidates)
        if not self.config.provider or self.config.top_k < 1:
            raise RerankingError("Enabled reranking requires a provider and positive top_k")
        try:
            ordered_ids = await self.registry.get(self.config.provider).rerank(
                query, candidates, top_k=self.config.top_k
            )
        except Exception as exc:
            raise RerankingError("Reranking provider failed") from exc
        by_id = {item.chunk_id: item for item in candidates}
        if len(set(ordered_ids)) != len(ordered_ids) or any(key not in by_id for key in ordered_ids):
            raise RerankingError("Reranker returned duplicate or unauthorized candidates")
        if len(ordered_ids) > self.config.top_k:
            raise RerankingError("Reranker returned more candidates than configured top_k")
        return [by_id[key].model_copy(update={"rerank_rank": rank})
                for rank, key in enumerate(ordered_ids, 1)]

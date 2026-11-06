"""Provider independent fusion of retrieval strategy results."""

from collections.abc import Sequence
from enum import Enum

from app.rag.models.retrieval import RetrievalResult


class FusionStrategy(str, Enum):
    RECIPROCAL_RANK = "reciprocal_rank"


class ResultFusion:
    """Fuse ranked lists using RRF, which does not compare raw score scales."""

    def __init__(self, *, strategy: FusionStrategy = FusionStrategy.RECIPROCAL_RANK,
                 rrf_constant: int = 60) -> None:
        if rrf_constant < 1:
            raise ValueError("rrf_constant must be positive")
        self.strategy = strategy
        self.rrf_constant = rrf_constant

    def fuse(self, dense: Sequence[RetrievalResult], lexical: Sequence[RetrievalResult],
             *, top_k: int) -> list[RetrievalResult]:
        if self.strategy != FusionStrategy.RECIPROCAL_RANK:
            raise ValueError(f"Unsupported fusion strategy: {self.strategy}")
        merged: dict[str, dict[str, object]] = {}
        for kind, results in (("dense", dense), ("lexical", lexical)):
            seen: set[str] = set()
            for rank, result in enumerate(results, 1):
                key = result.chunk_id
                if key in seen:
                    continue
                seen.add(key)
                entry = merged.setdefault(key, {"result": result, "dense_score": None,
                                                "lexical_score": None, "dense_rank": None,
                                                "lexical_rank": None, "fused_score": 0.0})
                entry[f"{kind}_score"] = result.score
                entry[f"{kind}_rank"] = rank
                entry["fused_score"] = float(entry["fused_score"]) + 1 / (self.rrf_constant + rank)
        ranked = sorted(merged.values(), key=lambda item: (-float(item["fused_score"]),
                                                           str(item["result"].chunk_id)))
        output = []
        for entry in ranked[:top_k]:
            result = entry["result"]
            output.append(result.model_copy(update={
                "score": entry["fused_score"], "fused_score": entry["fused_score"],
                "dense_score": entry["dense_score"], "lexical_score": entry["lexical_score"],
                "dense_rank": entry["dense_rank"], "lexical_rank": entry["lexical_rank"],
            }))
        return output

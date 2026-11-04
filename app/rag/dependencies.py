"""Dependency wiring for RAG services."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.core.config import Settings, get_settings
from app.rag.embeddings.base import EmbeddingConfig, ProviderSelectionError
from app.rag.embeddings.providers.google import GoogleEmbeddingProvider
from app.rag.embeddings.providers.huggingface import HuggingFaceEmbeddingProvider
from app.rag.embeddings.providers.openai import OpenAIEmbeddingProvider
from app.rag.embeddings.service import EmbeddingService

_PROVIDER_DEFAULTS: dict[str, tuple[str, int]] = {
    "openai": ("text-embedding-3-small", 1536),
    "google": ("gemini-embedding-001", 768),
    "huggingface": ("sentence-transformers/all-MiniLM-L6-v2", 384),
}
_CUSTOM_PROVIDER_BUILDERS: dict[str, Callable[..., Any]] = {}


def register_embedding_provider(name: str, builder: Callable[..., Any]) -> None:
    """Register an application-specific adapter selected by EMBEDDING_PROVIDER."""
    key = name.strip().lower()
    if not key or key in _PROVIDER_DEFAULTS:
        raise ValueError("provider name must be non-empty and must not replace a built-in provider")
    _CUSTOM_PROVIDER_BUILDERS[key] = builder


def build_embedding_service(
    settings: Settings | None = None,
    *,
    provider_builders: Mapping[str, Callable[..., Any]] | None = None,
) -> EmbeddingService:
    """Select one configured provider used for both query and document vectors."""
    settings = settings or get_settings()
    provider_name = settings.embedding_provider.strip().lower()
    defaults = _PROVIDER_DEFAULTS.get(provider_name)
    default_model, default_dimensions = defaults or (None, None)
    model = settings.embedding_model or default_model
    dimensions = settings.embedding_dimensions or default_dimensions
    builders: dict[str, Callable[[], Any]] = {
        "openai": lambda: OpenAIEmbeddingProvider(
            model=model or "", dimensions=dimensions or 0, api_key=settings.openai_api_key or "",
            base_url=settings.openai_base_url, timeout=settings.embedding_timeout_seconds,
        ),
        "google": lambda: GoogleEmbeddingProvider(
            model=model or "", dimensions=dimensions or 0, api_key=settings.gemini_api_key or "",
        ),
        "huggingface": lambda: HuggingFaceEmbeddingProvider(
            model=model or "", dimensions=dimensions or 0, token=settings.huggingface_api_token,
        ),
    }
    builders.update({name: builder for name, builder in _CUSTOM_PROVIDER_BUILDERS.items()})
    if provider_builders:
        builders.update({name.strip().lower(): builder for name, builder in provider_builders.items()})
    builder = builders.get(provider_name)
    if builder is None:
        raise ProviderSelectionError(f"Unsupported embedding provider: {provider_name}")
    if not model:
        raise ProviderSelectionError(f"Set EMBEDDING_MODEL for provider {provider_name}")
    provider = builder()
    dimensions = dimensions or getattr(provider, "dimensions", None)
    if not dimensions:
        raise ProviderSelectionError(f"Set EMBEDDING_DIMENSIONS for provider {provider_name}")
    collection_dimensions = getattr(settings, "qdrant_vector_size", dimensions)
    if collection_dimensions != dimensions:
        raise ProviderSelectionError(
            f"Embedding dimension {dimensions} does not match configured retrieval dimension {collection_dimensions}"
        )
    config = EmbeddingConfig(
        provider=provider_name,
        model=model,
        dimensions=dimensions,
        batch_size=settings.embedding_batch_size,
        max_retries=settings.embedding_max_retries,
        retry_backoff_seconds=settings.embedding_retry_backoff_seconds,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    return EmbeddingService(provider, config)

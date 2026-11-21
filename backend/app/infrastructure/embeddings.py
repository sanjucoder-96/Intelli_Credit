"""Embedding providers behind EmbeddingProviderPort (ADR-0007).

Default: fastembed's ``BAAI/bge-small-en-v1.5`` (384-dim, ONNX, CPU) — free,
deterministic, offline after the one-time model download; ingestion never
depends on API quota. Swapping to ``gemini-embedding-001`` is a config +
re-embed migration recorded in ADR-0007. The model loads lazily on first use
(workers that never embed never pay the load).
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger("app.infrastructure.embeddings")

FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
FASTEMBED_DIM = 384


class FastembedProvider:
    """EmbeddingProviderPort adapter over fastembed (ONNX, CPU)."""

    def __init__(self, model_name: str = FASTEMBED_MODEL, dimension: int = FASTEMBED_DIM) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    def _ensure_model(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    logger.info("loading_embedding_model", model=self._model_name)
                    self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns unit vectors of ``dimension``."""
        if not texts:
            return []
        model = self._ensure_model()
        vectors = [list(map(float, vector)) for vector in model.embed(texts)]
        for vector in vectors:
            if len(vector) != self._dimension:
                raise ValueError(
                    f"Embedding dimension {len(vector)} != configured {self._dimension} "
                    f"({self._model_name}); chunks column is vector({self._dimension})"
                )
        return vectors


_default_provider: FastembedProvider | None = None


def get_default_embedder() -> FastembedProvider:
    """Process-wide default embedding provider (ADR-0007).

    Lives here (not in a higher layer) so both the ingestion pipeline and the
    agent-runtime tools can share one lazily-loaded model without a layering
    violation. Tests monkeypatch this.
    """
    global _default_provider
    if _default_provider is None:
        _default_provider = FastembedProvider()
    return _default_provider

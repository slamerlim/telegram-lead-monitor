from __future__ import annotations

import structlog

log = structlog.get_logger()


class SemanticMatcher:
    """Optional multilingual semantic relevance layer.

    Disabled by default. When enabled, sentence-transformers is imported lazily so the
    baseline installation remains small and deterministic.
    """

    def __init__(self, enabled: bool, model_name: str, threshold: float) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.model = None
        self.reference = None
        if enabled:
            try:
                from sentence_transformers import SentenceTransformer

                self.model = SentenceTransformer(model_name)
                self.reference = self.model.encode(
                    [
                        "Client looking for a developer to build or fix a cryptocurrency trading bot",
                        "Need custom algorithmic trading software with exchange API and order execution",
                        "Looking for a developer for arbitrage, copy trading, market making or Solana DEX bot",
                    ],
                    normalize_embeddings=True,
                ).mean(axis=0)
            except Exception as exc:
                log.exception("semantic_model_unavailable", error=str(exc))
                self.enabled = False

    def score(self, text: str) -> float | None:
        if not self.enabled or self.model is None or self.reference is None:
            return None
        try:
            import numpy as np

            vector = self.model.encode([text], normalize_embeddings=True)[0]
            similarity = float(np.dot(vector, self.reference) / max(np.linalg.norm(self.reference), 1e-9))
            return max(0.0, min(1.0, similarity))
        except Exception as exc:
            log.warning("semantic_score_failed", error=str(exc))
            return None

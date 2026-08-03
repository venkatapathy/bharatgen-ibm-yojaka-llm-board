"""Reranking helpers for RAG retrieval."""

from functools import lru_cache

DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Shown in Control → Technical dropdowns (actual HF model ids).
RERANKER_CHOICES = (
    ("", "None"),
    (
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "MiniLM-L-6-v2 (recommended — light)",
    ),
    (
        "cross-encoder/ms-marco-TinyBERT-L-2-v2",
        "TinyBERT-L-2 (lighter, slightly weaker)",
    ),
)


@lru_cache(maxsize=4)
def _get_model(model_name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


def rerank_passages(query: str, passages, model_name: str = DEFAULT_RERANKER_MODEL, top_k: int | None = None):
    passages = [passage for passage in passages if passage]
    if not passages:
        return []
    model = _get_model(model_name)
    pairs = [[query, passage] for passage in passages]
    scores = model.predict(pairs)
    ranked = sorted(
        [{"text": passage, "score": float(score)} for passage, score in zip(passages, scores)],
        key=lambda item: item["score"],
        reverse=True,
    )
    if top_k:
        return ranked[:top_k]
    return ranked

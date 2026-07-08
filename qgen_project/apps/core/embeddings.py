"""Embedding helpers for the QGen demo."""

from functools import lru_cache

DEFAULT_EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"
DEFAULT_EMBED_DIMENSIONS = 768


@lru_cache(maxsize=4)
def _get_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def embed_texts(texts, model_name: str = DEFAULT_EMBED_MODEL):
    """Embed a list of texts using a 768-dim sentence-transformer."""
    texts = [text or "" for text in texts]
    if not texts:
        return []
    model = _get_model(model_name)
    vectors = model.encode(texts, normalize_embeddings=True)
    return vectors.tolist()

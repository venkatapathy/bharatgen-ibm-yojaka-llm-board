"""Embedding helpers for the QGen demo."""

from functools import lru_cache
import logging

import numpy as np

DEFAULT_EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"
FALLBACK_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBED_DIMENSIONS = 768

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _get_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    # Never hit the network — containers often cannot reach huggingface.co.
    return SentenceTransformer(model_name, local_files_only=True)


def _pad_to_dimensions(vectors, dimensions: int = DEFAULT_EMBED_DIMENSIONS):
    """Pad / truncate vectors to the pgvector column size and L2-normalize."""
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    width = arr.shape[1]
    if width < dimensions:
        arr = np.pad(arr, ((0, 0), (0, dimensions - width)), mode="constant")
    elif width > dimensions:
        arr = arr[:, :dimensions]
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return (arr / norms).tolist()


def embed_texts(texts, model_name: str = DEFAULT_EMBED_MODEL):
    """Embed a list of texts using a 768-dim sentence-transformer."""
    texts = [text or "" for text in texts]
    if not texts:
        return []

    tried = []
    for candidate in (model_name, FALLBACK_EMBED_MODEL):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        try:
            model = _get_model(candidate)
            vectors = model.encode(texts, normalize_embeddings=True)
            return _pad_to_dimensions(vectors)
        except Exception as exc:
            logger.warning("Embed model unavailable (%s): %s", candidate, exc)

    raise RuntimeError(
        f"No local embedding model available. Tried: {', '.join(tried)}"
    )

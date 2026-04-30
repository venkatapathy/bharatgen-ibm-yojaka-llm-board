"""
Shared SigLIP text/image embeddings for multimodal index build and retrieval.
Must use the same SIGLIP_MODEL_ID as when the index was built.
"""

from __future__ import annotations

import os

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SIGLIP_MODEL_ID = os.environ.get(
    "SIGLIP_MODEL_ID", "google/siglip-base-patch16-224"
)

# Optional override for per-book indexes (set before importing retrieve_mm / app).
INDEX_PATH = os.environ.get(
    "MM_RAG_INDEX_PATH", os.path.join(BASE_DIR, "outputs/mm_index.index")
)
META_PATH = os.environ.get(
    "MM_RAG_META_PATH", os.path.join(BASE_DIR, "outputs/mm_meta.pkl")
)
INDEX_ROOT = os.environ.get(
    "MM_RAG_INDEX_ROOT", os.path.join(BASE_DIR, "indexes", "all_books_english")
)

device = "cuda" if torch.cuda.is_available() else "cpu"

_processor = None
_model = None


def _get_model():
    global _processor, _model
    if _model is None:
        _processor = AutoProcessor.from_pretrained(SIGLIP_MODEL_ID)
        _model = AutoModel.from_pretrained(SIGLIP_MODEL_ID).to(device).eval()
    return _processor, _model


def _features_as_tensor(out):
    """SigLIP forward may return a tensor (older transformers) or BaseModelOutputWithPooling."""
    if torch.is_tensor(out):
        return out
    po = getattr(out, "pooler_output", None)
    if po is not None:
        return po
    te = getattr(out, "text_embeds", None)
    if te is not None:
        return te
    ie = getattr(out, "image_embeds", None)
    if ie is not None:
        return ie
    raise TypeError(f"Unexpected model output type: {type(out)}")


def text_max_length() -> int:
    """SigLIP text tower uses a short context (typically 64 tokens). Chunks in pdf_text_chunks are sized to match."""
    proc, _ = _get_model()
    tok = getattr(proc, "tokenizer", None)
    if tok is None:
        return 64
    ml = getattr(tok, "model_max_length", None) or 64
    if ml is None or ml > 10000:
        ml = 64
    return int(ml)


def embed_text(text: str) -> np.ndarray:
    proc, model = _get_model()
    max_len = text_max_length()
    inputs = proc(
        text=[text],
        padding="max_length",
        max_length=max_len,
        truncation=True,
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items() if k in ("input_ids", "attention_mask")}
    with torch.inference_mode():
        raw = model.get_text_features(**inputs)
    feat = _features_as_tensor(raw)
    feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat.cpu().numpy()[0].astype(np.float32)


def embed_image(pil_rgb: Image.Image) -> np.ndarray:
    proc, model = _get_model()
    inputs = proc(images=pil_rgb, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items() if k == "pixel_values"}
    with torch.inference_mode():
        raw = model.get_image_features(**inputs)
    feat = _features_as_tensor(raw)
    feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat.cpu().numpy()[0].astype(np.float32)


def fix_image_path(path: str) -> str:
    if os.path.exists(path):
        return path
    return os.path.join(BASE_DIR, "outputs/ncert_figures_v6", os.path.basename(path))

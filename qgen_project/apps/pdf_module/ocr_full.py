"""Rebuild full per-PDF OCR text (not truncated hierarchical chunks)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile

from apps.pdf_module.chunkers import (
    clean_page_text,
    clean_unlimited_ocr_text,
    is_indexable_chunk,
    ocr_page_text,
)
from apps.pdf_module.legacy_hindi import (
    looks_like_legacy_hindi,
    normalize_legacy_hindi,
    page_uses_legacy_hindi_font,
)
from apps.pdf_module.models import PDFContext

logger = logging.getLogger(__name__)


def _normalize_full(text: str) -> str:
    if not text:
        return ""
    is_leg, ft = looks_like_legacy_hindi(text)
    if is_leg:
        return normalize_legacy_hindi(text, force=True, font_type=ft or "krutidev")
    return text


def extract_full_ocr_text(pdf_path: str, *, force_vision: bool = False) -> str:
    """Full document text: prefer rich native (+ legacy remap), else Unlimited-OCR."""
    import fitz

    doc = fitz.open(pdf_path)
    blocks: list[str] = []
    for page_number, page in enumerate(doc, start=1):
        raw = page.get_text() or ""
        force_legacy = page_uses_legacy_hindi_font(page)
        if not force_legacy:
            is_leg, _ = looks_like_legacy_hindi(raw)
            force_legacy = is_leg
        native = clean_page_text(raw, force_legacy=force_legacy)
        native = _normalize_full(native)

        use_native = (
            not force_vision
            and native
            and is_indexable_chunk(native, min_tokens=12)
        )
        if use_native:
            text = native
        else:
            try:
                ocr_raw = ocr_page_text(page)
            except Exception as exc:
                logger.warning("Vision OCR skipped page %s: %s", page_number, exc)
                ocr_raw = ""
            text = clean_page_text(ocr_raw, force_legacy=False) if ocr_raw else native
            text = _normalize_full(text)
            # If vision failed, keep native even if short.
            if not text.strip():
                text = native

        if text and text.strip():
            blocks.append(f"===== PAGE {page_number} =====\n{text.strip()}")
    return "\n\n".join(blocks).strip()


def rebuild_context_ocr(ctx: PDFContext, *, force_vision: bool = False) -> int:
    """Overwrite ctx.ocr_text with full-document extract. Returns char count."""
    path = ctx.zip_path.path if ctx.zip_path else ""
    if not path:
        return 0

    parts: list[str] = []
    if path.lower().endswith(".zip"):
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(path, "r") as archive:
                archive.extractall(tmpdir)
            for root, _, files in os.walk(tmpdir):
                for name in sorted(files):
                    if name.lower().endswith(".pdf"):
                        parts.append(
                            extract_full_ocr_text(
                                os.path.join(root, name), force_vision=force_vision
                            )
                        )
    elif path.lower().endswith(".pdf"):
        parts.append(extract_full_ocr_text(path, force_vision=force_vision))
    else:
        return 0

    text = "\n\n".join(p for p in parts if p).strip()
    ctx.ocr_text = text
    ctx.save(update_fields=["ocr_text"])
    return len(text)

"""Rebuild full per-PDF OCR text (not truncated hierarchical chunks)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile

from apps.pdf_module.chunkers import (
    clean_page_text,
    collapse_ocr_repetition,
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


def extract_full_ocr_text(
    pdf_path: str, *, force_vision: bool = False, native_only: bool = False
) -> str:
    """Full document text: prefer rich native (+ legacy remap), else Unlimited-OCR.

    native_only=True: never call vision OCR (fast path for upload HTTP requests).
    """
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
            and (native_only or is_indexable_chunk(native, min_tokens=12))
        )
        if use_native:
            text = native
        elif native_only:
            # Keep whatever native text exists; skip vision.
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

        text = collapse_ocr_repetition(text or "")
        if text and text.strip():
            blocks.append(f"===== PAGE {page_number} =====\n{text.strip()}")
    return "\n\n".join(blocks).strip()


def rebuild_context_ocr(
    ctx: PDFContext, *, force_vision: bool = False, native_only: bool = False
) -> int:
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
                                os.path.join(root, name),
                                force_vision=force_vision,
                                native_only=native_only,
                            )
                        )
    elif path.lower().endswith(".pdf"):
        parts.append(
            extract_full_ocr_text(
                path, force_vision=force_vision, native_only=native_only
            )
        )
    else:
        return 0

    text = "\n\n".join(p for p in parts if p).strip()
    ctx.ocr_text = text
    ctx.save(update_fields=["ocr_text"])
    return len(text)


def clean_stored_ocr_text(ctx: PDFContext) -> int:
    """Re-clean existing ocr_text (collapse loops / strip noise) without re-OCR."""
    from apps.pdf_module.chunkers import sanitize_ocr_text

    raw = (ctx.ocr_text or "").strip()
    if not raw:
        return 0
    # Preserve page separators; clean each page body independently.
    parts: list[str] = []
    for block in re.split(r"(?m)(?=^===== PAGE \d+ =====\s*$)", raw):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"(===== PAGE \d+ =====)\s*(.*)", block, re.DOTALL)
        if m:
            header, body = m.group(1), m.group(2)
            cleaned = sanitize_ocr_text(body)
            if cleaned.strip():
                parts.append(f"{header}\n{cleaned.strip()}")
        else:
            cleaned = sanitize_ocr_text(block)
            if cleaned.strip():
                parts.append(cleaned.strip())
    text = "\n\n".join(parts).strip()
    ctx.ocr_text = text
    ctx.save(update_fields=["ocr_text"])
    return len(text)

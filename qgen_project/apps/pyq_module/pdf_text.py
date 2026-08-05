"""PDF text extraction helpers for PYQ ingestion.

Prefers native text when usable; otherwise Unlimited-OCR (GPU) via
``apps.pdf_module.chunkers``, then cleans for LLM question extraction.
"""

from __future__ import annotations

import logging
import re

from apps.pdf_module.legacy_hindi import (
    normalize_legacy_hindi,
    page_uses_legacy_hindi_font,
)

logger = logging.getLogger(__name__)

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_EN_WORD = re.compile(r"[A-Za-z]{3,}")


def _is_usable_pyq_text(text: str) -> bool:
    """True when native/legacy-decoded text is good enough without vision OCR."""
    t = (text or "").strip()
    if len(t) < 80:
        return False
    if _DEVANAGARI.search(t):
        return len(t.split()) >= 20
    # English / bilingual Latin: need real words (rejects KrutiDev garbage).
    return len(_EN_WORD.findall(t)) >= 25


def extract_text(path: str):
    import fitz

    from apps.pdf_module.chunkers import clean_page_text, ocr_page_text

    doc = fitz.open(path)
    pages = []
    for page_number, page in enumerate(doc, start=1):
        force_legacy = page_uses_legacy_hindi_font(page)
        native = (page.get_text() or "").strip()
        if native:
            native = normalize_legacy_hindi(native, force=force_legacy)
            native = clean_page_text(native, force_legacy=False)

        if _is_usable_pyq_text(native):
            text = native
            source = "native"
        else:
            ocr_raw = ocr_page_text(page)
            text = clean_page_text(ocr_raw, force_legacy=False) if ocr_raw else native
            source = "unlimited-ocr" if ocr_raw else "native-weak"
            if source == "unlimited-ocr":
                logger.info(
                    "PYQ page %s OCR via Unlimited-OCR (%s chars)",
                    page_number,
                    len(text),
                )

        if text and len(text.strip()) >= 20:
            pages.append(
                {
                    "page_number": page_number,
                    "text": text.strip(),
                    "source": source,
                }
            )
    return pages


def chunk_text(text: str, max_words=900):
    parts = [line.strip() for line in re.split(r"\n\s*\n", text) if line.strip()]
    chunks = []
    current = []
    current_words = 0
    for part in parts:
        words = len(part.split())
        current.append(part)
        current_words += words
        if current_words >= max_words:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
    if current:
        chunks.append("\n\n".join(current))
    return chunks

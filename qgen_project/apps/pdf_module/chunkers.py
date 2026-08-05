"""Chunking helpers for PDF ingestion.

All strategies use **word** counts for chunk_size / chunk_overlap (same units as
the Technical settings UI). If a non-fixed strategy errors or yields nothing
indexable, we silently fall back to fixed_size.
"""

from __future__ import annotations

import logging
import math
import os
import re
from typing import Callable, List, Optional, Sequence

from .legacy_hindi import (
    normalize_legacy_hindi,
    page_uses_legacy_hindi_font,
)
from .latin_encoding import repair_shifted_latin_text

logger = logging.getLogger(__name__)

_PAGE_NUM_PREFIX = re.compile(r"^\s*\d{1,4}\b\s*")
_DOTS = re.compile(r"\.{5,}")
_HEADER_LINE = re.compile(
    r"(?im)^\s*(?:BHD(?:C|LA|S)[-\s]?\d+|BEGC[-\s]?\d+|e-?gyankosh|ignou|"
    r"bafnjk\s+xka/kh|ekufodh\s+fo\|kihB|"
    r"मानविकी\s+विद्यापीठ|इग्नू|इन्दिरा\s+गाँधी).*$"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+")


def clean_page_text(text: str, *, force_legacy: bool = False) -> str:
    """Strip IGNOU noise and remap legacy Hindi fonts to Unicode Devanagari."""
    if not text:
        return ""
    text = repair_shifted_latin_text(text)
    text = _DOTS.sub(" ", text)
    text = _HEADER_LINE.sub("", text)
    text = _PAGE_NUM_PREFIX.sub("", text, count=1)
    text = normalize_legacy_hindi(text, force=force_legacy)
    text = _HEADER_LINE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_indexable_chunk(text: str, min_tokens: int = 30) -> bool:
    """Drop tiny / digit-only / punctuation-only fragments from the index."""
    if not text:
        return False
    words = text.split()
    if len(words) < min_tokens:
        return False
    if re.fullmatch(r"[\d\s.\-–—_/\\]+", text):
        return False
    alnum = re.sub(r"[^\w]+", "", text, flags=re.UNICODE)
    return len(alnum) >= 40


_OCR_LANGS_CACHE: Optional[str] = None

# Unlimited-OCR (Ollama vision) — strip layout tags / model chatter before indexing.
_UOCR_FOOTER = re.compile(r"^\s*footer\b", re.IGNORECASE)
_UOCR_NOISE = re.compile(
    r"^\s*(Do not use|Special formatting rules|The content provided|"
    r"If no valid OCR|If there is no actual text|Therefore,?\s+the corrected OCR|"
    r"The OCR should output|No text detected)\b",
    re.IGNORECASE,
)
_UOCR_BOX = re.compile(
    r"^\s*(?:title|text|header|image|table|caption)\s+\[[^\]]+\]\s*(.*)$",
    re.IGNORECASE,
)
_UOCR_META_INLINE = re.compile(
    r"(?is)(?:```(?:text)?\s*)?\[?\s*No text detected\s*\]?\s*"
    r"|If no valid OCR output is provided for any content\.?\s*"
    r"|If there is no actual text content in the source image[^.]*\.\s*"
    r"|Therefore,?\s+the corrected OCR text is:\s*"
    r"|The OCR should output nothing\.?\s*",
)
_UOCR_FENCE = re.compile(r"```(?:text)?|```")


def clean_unlimited_ocr_text(text: str) -> str:
    """Strip Unlimited-OCR bbox / footer / instruction noise → plain text."""
    if not text:
        return ""
    text = _UOCR_META_INLINE.sub(" ", text)
    text = _UOCR_FENCE.sub("", text)
    out: list[str] = []
    for line in text.splitlines():
        if _UOCR_FOOTER.match(line) or _UOCR_NOISE.match(line):
            continue
        if re.search(r"\[?\s*No text detected\s*\]?", line, re.I) and len(line.split()) < 12:
            continue
        m = _UOCR_BOX.match(line)
        if m:
            content = m.group(1).strip()
            if content and content != "[Non-Text]" and not re.fullmatch(
                r"\[?\s*No text detected\s*\]?", content, re.I
            ):
                out.append(content)
            continue
        out.append(line.rstrip())
    cleaned = "\n".join(out)
    # Unlimited-OCR sometimes emits HTML table markup for ToC blocks.
    cleaned = re.sub(r"</?(?:table|tr|td|th|tbody|thead)[^>]*>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _unlimited_ocr_config() -> tuple[str, str]:
    import os

    base = (
        os.environ.get("UNLIMITED_OCR_URL")
        or os.environ.get("OCR_OLLAMA_URL")
        or "http://10.129.6.47:11441"
    ).rstrip("/")
    if base.endswith("/api/generate"):
        url = base
    else:
        url = f"{base}/api/generate"
    model = os.environ.get("UNLIMITED_OCR_MODEL") or "frob/unlimited-ocr:q8_0"
    return url, model


def ocr_page_unlimited(page, *, dpi: float = 144, retries: int = 2) -> str:
    """OCR a page via Unlimited-OCR on GPU Ollama (preferred path)."""
    import base64
    import time

    import fitz
    import requests

    url, model = _unlimited_ocr_config()
    zoom = max(dpi / 72.0, 1.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    png_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
    prompt = "Extract all text from this document accurately, maintaining the structure."
    last = ""
    for attempt in range(retries + 1):
        try:
            r = requests.post(
                url,
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "60m",
                    "images": [png_b64],
                },
                timeout=300,
            )
            r.raise_for_status()
            last = (r.json().get("response") or "").strip()
            if len(last) >= 40:
                return clean_unlimited_ocr_text(last)
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
        except Exception as exc:
            logger.warning("Unlimited-OCR failed (attempt %s): %s", attempt + 1, exc)
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    return clean_unlimited_ocr_text(last)


def _tesseract_langs() -> str:
    global _OCR_LANGS_CACHE
    if _OCR_LANGS_CACHE is not None:
        return _OCR_LANGS_CACHE
    try:
        import pytesseract

        available = set(pytesseract.get_languages(config=""))
        if "hin" in available and "eng" in available:
            _OCR_LANGS_CACHE = "hin+eng"
        elif "hin" in available:
            _OCR_LANGS_CACHE = "hin"
        else:
            _OCR_LANGS_CACHE = "eng"
    except Exception:
        _OCR_LANGS_CACHE = "eng"
    return _OCR_LANGS_CACHE


def ocr_page_tesseract(page, *, dpi: float = 150) -> str:
    """Local tesseract fallback when Unlimited-OCR is unavailable."""
    try:
        import io

        import fitz
        import pytesseract
        from PIL import Image

        zoom = max(dpi / 72.0, 1.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(image, lang=_tesseract_langs())
        return (text or "").strip()
    except Exception as exc:
        logger.warning("Tesseract OCR failed on page: %s", exc)
        return ""


def ocr_page_text(page, *, dpi: float = 144) -> str:
    """OCR a PyMuPDF page: Unlimited-OCR (GPU) first, then tesseract."""
    text = ocr_page_unlimited(page, dpi=dpi)
    if text and len(text.split()) >= 5:
        return text
    return ocr_page_tesseract(page, dpi=max(dpi, 150))


def _force_unlimited_ocr() -> bool:
    """Prefer Unlimited-OCR on every page for quality text (default on)."""
    raw = (os.environ.get("PDF_FORCE_UNLIMITED_OCR") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def extract_pages_from_pdf(path: str):
    import fitz

    doc = fitz.open(path)
    pages = []
    force_ocr = _force_unlimited_ocr()
    for page_number, page in enumerate(doc, start=1):
        text = ""
        if force_ocr:
            # Always Unlimited-OCR (GPU) → tesseract fallback for quality text.
            ocr_text = ocr_page_text(page)
            if ocr_text:
                text = clean_page_text(ocr_text, force_legacy=False)
        if not text:
            force_legacy = page_uses_legacy_hindi_font(page)
            text = clean_page_text(page.get_text(), force_legacy=force_legacy)
            # Native extract can still be KrutiDev even when font names are odd.
            from .legacy_hindi import looks_like_legacy_hindi, normalize_legacy_hindi

            is_leg, ft = looks_like_legacy_hindi(text)
            if is_leg:
                text = normalize_legacy_hindi(text, force=True, font_type=ft or "krutidev")
            if not force_ocr and (
                not text or not is_indexable_chunk(text, min_tokens=10)
            ):
                ocr_text = ocr_page_text(page)
                if ocr_text:
                    text = clean_page_text(ocr_text, force_legacy=False)
        if text and is_indexable_chunk(text, min_tokens=10):
            pages.append(
                {
                    "page_number": page_number,
                    "text": text,
                    "source_file": os.path.basename(path),
                }
            )
    return pages


def extract_text_from_pdf(path: str) -> str:
    return "\n\n".join(page["text"] for page in extract_pages_from_pdf(path))


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _split_sentences(text: str) -> List[str]:
    try:
        import nltk

        try:
            sentences = nltk.sent_tokenize(text)
        except LookupError:
            nltk.download("punkt", quiet=True)
            nltk.download("punkt_tab", quiet=True)
            sentences = nltk.sent_tokenize(text)
    except Exception:
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return [s.strip() for s in sentences if s and s.strip()]


def _pack_units(
    units: Sequence[str],
    *,
    chunk_size: int,
    joiner: str = " ",
) -> List[str]:
    """Greedily pack text units up to chunk_size words; oversized units are split."""
    chunk_size = max(int(chunk_size), 1)
    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    def flush():
        nonlocal current, current_words
        if current:
            chunks.append(joiner.join(current).strip())
            current = []
            current_words = 0

    for unit in units:
        unit = (unit or "").strip()
        if not unit:
            continue
        uw = _word_count(unit)
        if uw > chunk_size:
            flush()
            chunks.extend(
                fixed_size_chunker(unit, chunk_size=chunk_size, chunk_overlap=0)
            )
            continue
        if current and current_words + uw > chunk_size:
            flush()
        current.append(unit)
        current_words += uw
    flush()
    return [c for c in chunks if c]


def fixed_size_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    """Sliding window over words (chunk_size / overlap are word counts)."""
    words = text.split()
    if not words:
        return []
    chunk_size = max(int(chunk_size), 1)
    chunk_overlap = max(min(int(chunk_overlap), chunk_size - 1), 0)
    step = max(chunk_size - chunk_overlap, 1)
    chunks = []
    start = 0
    while start < len(words):
        piece = " ".join(words[start : start + chunk_size]).strip()
        if piece:
            chunks.append(piece)
        if start + chunk_size >= len(words):
            break
        start += step
    return chunks


def sentence_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    """Pack whole sentences until ~chunk_size words."""
    sentences = _split_sentences(text)
    if not sentences:
        return fixed_size_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return _pack_units(sentences, chunk_size=chunk_size, joiner=" ")


def paragraph_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    """Prefer paragraph boundaries; pack small paras / split oversized ones."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        # PDF text often lacks blank lines — fall back to sentence packing.
        return sentence_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return _pack_units(paragraphs, chunk_size=chunk_size, joiner="\n\n")


def _recursive_split_words(
    text: str,
    separators: Sequence[str],
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if _word_count(text) <= chunk_size:
        return [text]

    if not separators:
        return fixed_size_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    sep = separators[0]
    rest = separators[1:]
    if sep:
        parts = [p for p in text.split(sep) if p.strip()]
    else:
        parts = [text]

    if len(parts) <= 1:
        return _recursive_split_words(text, rest, chunk_size, chunk_overlap)

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    def join_parts(items: List[str]) -> str:
        return sep.join(items).strip() if sep else " ".join(items).strip()

    for part in parts:
        pw = _word_count(part)
        if pw > chunk_size:
            if current:
                chunks.append(join_parts(current))
                current, current_words = [], 0
            chunks.extend(_recursive_split_words(part, rest, chunk_size, chunk_overlap))
            continue
        if current and current_words + pw > chunk_size:
            chunks.append(join_parts(current))
            # Overlap: keep trailing words from previous chunk as soft context.
            if chunk_overlap > 0 and current:
                overlap_text = join_parts(current)
                overlap_words = overlap_text.split()[-chunk_overlap:]
                current = [" ".join(overlap_words)] if overlap_words else []
                current_words = _word_count(current[0]) if current else 0
            else:
                current, current_words = [], 0
        current.append(part)
        current_words += pw

    if current:
        chunks.append(join_parts(current))
    return [c for c in chunks if c]


def recursive_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    """
    Hierarchical split on paragraph → line → sentence → clause → space,
    targeting word-based chunk_size (not character size).
    """
    separators = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "]
    return _recursive_split_words(
        text,
        separators,
        chunk_size=max(int(chunk_size), 1),
        chunk_overlap=max(int(chunk_overlap), 0),
    )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def semantic_chunker(
    text: str,
    chunk_size=512,
    chunk_overlap=64,
    embed_fn: Optional[Callable] = None,
    **kwargs,
) -> List[str]:
    """
    Split near embedding similarity drops between consecutive sentences,
    while packing up to chunk_size words.
    """
    sentences = _split_sentences(text)
    if len(sentences) < 3 or embed_fn is None:
        return sentence_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    try:
        vectors = embed_fn(sentences)
    except Exception as exc:
        logger.warning("Semantic embed failed (%s); using sentence chunker", exc)
        return sentence_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    if (
        not vectors
        or len(vectors) != len(sentences)
        or any(v is None for v in vectors)
    ):
        return sentence_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    sims = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
    if sims:
        # Break when similarity is well below the local average (topic shift).
        mean_sim = sum(sims) / len(sims)
        threshold = min(mean_sim * 0.85, mean_sim - 0.05)
    else:
        threshold = 0.5

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0
    chunk_size = max(int(chunk_size), 1)

    def flush():
        nonlocal current, current_words
        if current:
            chunks.append(" ".join(current).strip())
            current = []
            current_words = 0

    for i, sentence in enumerate(sentences):
        sw = _word_count(sentence)
        if sw > chunk_size:
            flush()
            chunks.extend(
                fixed_size_chunker(sentence, chunk_size=chunk_size, chunk_overlap=0)
            )
            continue

        would_exceed = current and current_words + sw > chunk_size
        topic_break = (
            current
            and i > 0
            and i - 1 < len(sims)
            and sims[i - 1] < threshold
            and current_words >= max(chunk_size // 3, 50)
        )
        if would_exceed or topic_break:
            flush()

        current.append(sentence)
        current_words += sw

    flush()
    return [c for c in chunks if c] or sentence_chunker(
        text, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )


STRATEGY_MAP = {
    "hierarchical": None,  # document-level; see hierarchical_chunk_texts / tasks
    "fixed_size": fixed_size_chunker,
    "sentence": sentence_chunker,
    "paragraph": paragraph_chunker,
    "recursive": recursive_chunker,
    "semantic": semantic_chunker,
}


def chunk_page_text(
    page_text: str,
    strategy: str,
    *,
    chunk_size=512,
    chunk_overlap=64,
    embed_fn=None,
):
    page_text = clean_page_text(page_text)
    if not page_text:
        return []

    strategy = (strategy or "hierarchical").strip()
    # Hierarchical needs full-document text — page path falls back to fixed_size.
    if strategy == "hierarchical":
        strategy = "fixed_size"
    chunk_size = max(int(chunk_size or 512), 1)
    chunk_overlap = max(int(chunk_overlap or 0), 0)

    def _run(name: str) -> List[str]:
        chunker = STRATEGY_MAP.get(name) or fixed_size_chunker
        raw = chunker(
            page_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embed_fn=embed_fn,
        )
        return [c for c in (raw or []) if is_indexable_chunk(c)]

    if strategy == "fixed_size":
        return _run("fixed_size")

    try:
        chunks = _run(strategy)
        if chunks:
            return chunks
        logger.warning(
            "Chunking strategy %s produced no indexable chunks; falling back to fixed_size",
            strategy,
        )
    except Exception as exc:
        logger.warning(
            "Chunking strategy %s failed (%s); falling back to fixed_size",
            strategy,
            exc,
        )

    try:
        return _run("fixed_size")
    except Exception as exc:
        # Last resort — never raise to the caller/UI.
        logger.warning("Fixed-size fallback also failed (%s)", exc)
        return []

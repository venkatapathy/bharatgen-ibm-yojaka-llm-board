"""Chunking helpers for PDF ingestion."""

import os
import re
from typing import List

from .legacy_hindi import (
    normalize_legacy_hindi,
    page_uses_legacy_hindi_font,
)

_PAGE_NUM_PREFIX = re.compile(r"^\s*\d{1,4}\b\s*")
_DOTS = re.compile(r"\.{5,}")
_HEADER_LINE = re.compile(
    r"(?im)^\s*(?:BHD(?:C|LA|S)[-\s]?\d+|BEGC[-\s]?\d+|e-?gyankosh|ignou|"
    r"bafnjk\s+xka/kh|ekufodh\s+fo\|kihB|"
    r"मानविकी\s+विद्यापीठ|इग्नू|इन्दिरा\s+गाँधी).*$"
)


def clean_page_text(text: str, *, force_legacy: bool = False) -> str:
    """Strip IGNOU noise and remap legacy Hindi fonts to Unicode Devanagari."""
    if not text:
        return ""
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


def extract_pages_from_pdf(path: str):
    import fitz

    doc = fitz.open(path)
    pages = []
    for page_number, page in enumerate(doc, start=1):
        force_legacy = page_uses_legacy_hindi_font(page)
        text = clean_page_text(page.get_text(), force_legacy=force_legacy)
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


def fixed_size_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    words = text.split()
    chunks = []
    start = 0
    step = max(chunk_size - chunk_overlap, 1)
    while start < len(words):
        chunks.append(" ".join(words[start : start + chunk_size]).strip())
        start += step
    return [chunk for chunk in chunks if chunk]


def sentence_chunker(text: str, chunk_size=512, **kwargs) -> List[str]:
    try:
        import nltk

        try:
            sentences = nltk.sent_tokenize(text)
        except LookupError:
            nltk.download("punkt", quiet=True)
            sentences = nltk.sent_tokenize(text)
    except ImportError:
        sentences = [line.strip() for line in re.split(r"(?<=[.!?])\s+", text) if line.strip()]

    chunks = []
    current = []
    for sentence in sentences:
        current.append(sentence)
        if len(" ".join(current).split()) >= chunk_size:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def paragraph_chunker(text: str, **kwargs) -> List[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def recursive_chunker(text: str, chunk_size=1000, chunk_overlap=120, **kwargs) -> List[str]:
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return splitter.split_text(text)
    except ImportError:
        return fixed_size_chunker(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def semantic_chunker(text: str, chunk_size=900, embed_fn=None, **kwargs) -> List[str]:
    paragraphs = paragraph_chunker(text)
    if not embed_fn or len(paragraphs) < 3:
        return recursive_chunker(text, chunk_size=chunk_size)
    chunks = []
    current = []
    current_words = 0
    for paragraph in paragraphs:
        current.append(paragraph)
        current_words += len(paragraph.split())
        if current_words >= chunk_size:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_page_text(page_text: str, strategy: str, *, chunk_size=512, chunk_overlap=64, embed_fn=None):
    page_text = clean_page_text(page_text)
    if not page_text:
        return []
    chunker = STRATEGY_MAP.get(strategy, fixed_size_chunker)
    chunks = chunker(
        page_text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        embed_fn=embed_fn,
    )
    return [c for c in chunks if is_indexable_chunk(c)]


STRATEGY_MAP = {
    "fixed_size": fixed_size_chunker,
    "sentence": sentence_chunker,
    "paragraph": paragraph_chunker,
    "recursive": recursive_chunker,
    "semantic": semantic_chunker,
}

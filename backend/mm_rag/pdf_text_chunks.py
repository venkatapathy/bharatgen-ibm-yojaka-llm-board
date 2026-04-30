"""
Simple text-layer PDF extraction for CLI and RAG chunking.

Usage:
    python pdf_text_chunks.py /path/to/book.pdf
    python pdf_text_chunks.py /path/to/book.pdf --chunks
    python pdf_text_chunks.py /path/to/book.pdf --page 5
    python pdf_text_chunks.py /path/to/book.pdf --pages 1 10 -o out.txt
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

# Allow pypdf to decompress large streams for books with heavy content streams.
os.environ.setdefault("PYPDF_ZLIB_MAX_OUTPUT_LENGTH", "0")

DEFAULT_CHUNK_WORDS   = int(os.environ.get("MM_RAG_CHUNK_WORDS",   "48"))
DEFAULT_CHUNK_OVERLAP = int(os.environ.get("MM_RAG_CHUNK_OVERLAP", "12"))

# ── Helpers ───────────────────────────────────────────────────────────────────
def is_readable_text(text: str, min_alpha_ratio: float = 0.35) -> bool:
    cleaned = re.sub(r"\s+", "", text or "")
    if not cleaned:
        return False
    alpha = sum(ch.isalpha() for ch in cleaned)
    return (alpha / len(cleaned)) >= min_alpha_ratio


# ── Extraction ────────────────────────────────────────────────────────────────
def _read_pages_pymupdf(
    path: str,
    page_range: tuple[int, int] | None = None,
    require_readable: bool = True,
) -> list[tuple[int, str]]:
    """Fallback text-layer reader (PyMuPDF)."""
    try:
        import fitz
    except Exception:
        return []

    doc = fitz.open(path)
    try:
        start = (page_range[0] - 1) if page_range else 0
        end = page_range[1] if page_range else len(doc)
        results = []
        for i in range(start, min(end, len(doc))):
            text = doc[i].get_text("text") or ""
            if (not require_readable and text.strip()) or is_readable_text(text):
                results.append((i + 1, text.strip()))
        return results
    finally:
        doc.close()


def read_pages(path: str, page_range: tuple[int, int] | None = None) -> list[tuple[int, str]]:
    """Return (page_number_1based, text) for readable text-layer pages."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = reader.pages

    start = (page_range[0] - 1) if page_range else 0
    end = page_range[1] if page_range else len(pages)

    results = []
    for i in range(start, min(end, len(pages))):
        text = ""
        try:
            text = pages[i].extract_text() or ""
        except Exception:
            # Fall back to PyMuPDF for problematic pages (e.g., decompress limits).
            fallback = _read_pages_pymupdf(path, page_range=(i + 1, i + 1))
            if fallback:
                results.extend(fallback)
            continue

        if is_readable_text(text):
            results.append((i + 1, text.strip()))

    # Some PDFs (e.g., subset fonts) are unreadable in pypdf but readable in PyMuPDF.
    if not results:
        fallback = _read_pages_pymupdf(path, page_range, require_readable=True)
        if fallback:
            return fallback
        # Last resort for symbol-heavy textbooks (e.g. some Maths PDFs).
        loose_results = []
        for i in range(start, min(end, len(pages))):
            try:
                text = pages[i].extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                loose_results.append((i + 1, text.strip()))
        if loose_results:
            print("⚠️ Using non-filtered text fallback for low-alpha PDF pages.", flush=True)
            return loose_results
        return _read_pages_pymupdf(path, page_range, require_readable=False)
    return results


def read_pdf(path: str) -> str:
    """Return concatenated readable text from all pages."""
    return " ".join(text for _, text in read_pages(path))


# ── Chunking (from existing code) ─────────────────────────────────────────────
def chunk_text(
    text: str,
    size: int = DEFAULT_CHUNK_WORDS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Sliding-window word chunks — same logic as the existing codebase."""
    if overlap >= size:
        overlap = max(0, size // 4)
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + size]))
        if i + size >= len(words):
            break
        i += step
    return chunks


def extract_text_chunks(
    pdf_path: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """
    Import API used by build_multimodal_index.py.
    Returns overlapping word chunks from readable text-layer pages.
    """
    size = DEFAULT_CHUNK_WORDS if chunk_size is None else chunk_size
    ov = DEFAULT_CHUNK_OVERLAP if overlap is None else overlap
    text = read_pdf(pdf_path)
    if not text.strip():
        return []
    return chunk_text(text, size=size, overlap=ov)


# ── CLI actions ───────────────────────────────────────────────────────────────
def extract_full(path: str, output: str, page_range: tuple[int, int] | None = None) -> None:
    print(f"📖  Reading: {path}")
    pages = read_pages(path, page_range)
    if not pages:
        print("⚠️  No readable text found. The PDF may be scanned — try OCR mode.")
        return

    lines = [f"--- Page {pno} ---\n{text}" for pno, text in pages]
    full  = "\n\n".join(lines)

    with open(output, "w", encoding="utf-8") as f:
        f.write(full)

    print(f"✅  Extracted {len(pages)} pages → {output}")
    print(f"    Total characters : {len(full):,}")
    print(f"    Total words      : {len(full.split()):,}")


def extract_chunks(
    path: str,
    output: str,
    chunk_size: int = DEFAULT_CHUNK_WORDS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> None:
    print(f"📖  Reading: {path}")
    pages = read_pages(path)
    full_txt = " ".join(text for _, text in pages)

    if not full_txt.strip():
        print("⚠️  No readable text found.")
        return

    chunks = chunk_text(full_txt, size=chunk_size, overlap=overlap)
    chunk_output = output.replace(".txt", "_chunks.txt")

    with open(chunk_output, "w", encoding="utf-8") as f:
        for idx, chunk in enumerate(chunks, 1):
            f.write(f"[CHUNK {idx}]\n{chunk}\n\n")

    print(f"✅  {len(chunks)} chunks (size={chunk_size}, overlap={overlap}) → {chunk_output}")


def preview_page(path: str, page: int) -> None:
    pages = read_pages(path, page_range=(page, page))
    if not pages:
        print(f"⚠️  Page {page} has no extractable text.")
        return
    _, text = pages[0]
    print(f"\n{'='*60}\n Page {page}\n{'='*60}\n{text}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Simple text-layer extraction from PDF")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", default=None, help="Output .txt file")
    parser.add_argument("--chunks", action="store_true", help="Also export RAG-ready word chunks")
    parser.add_argument("--page",   type=int,           help="Preview a single page")
    parser.add_argument("--pages", type=int, nargs=2, help="Extract page range: --pages START END")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_WORDS)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    args = parser.parse_args()

    pdf_path = os.path.abspath(args.pdf)
    if not os.path.isfile(pdf_path):
        parser.error(f"PDF not found: {pdf_path}")

    output_path = args.output
    if output_path is None:
        output_path = str(Path(pdf_path).with_name(f"{Path(pdf_path).stem}_extracted.txt"))

    if args.page:
        preview_page(pdf_path, args.page)
        return

    page_range = tuple(args.pages) if args.pages else None  # type: ignore[arg-type]

    extract_full(pdf_path, output_path, page_range=page_range)

    if args.chunks:
        extract_chunks(
            pdf_path,
            output_path,
            chunk_size=args.chunk_size,
            overlap=args.chunk_overlap,
        )


if __name__ == "__main__":
    main()
"""PDF text extraction helpers for PYQ ingestion."""

import re


def extract_text(path: str):
    import fitz

    doc = fitz.open(path)
    pages = []
    for page_number, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        if not text:
            try:
                import pytesseract
                from PIL import Image
                import io

                pix = page.get_pixmap()
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(image).strip()
            except Exception:
                text = ""
        if text:
            pages.append({"page_number": page_number, "text": text})
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

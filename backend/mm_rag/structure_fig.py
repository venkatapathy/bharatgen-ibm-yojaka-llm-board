"""
Week 2 — Multimodal Data Structuring (fixed multiline captions)
================================================================
Key fixes over previous version:
  1. PDF text layer is garbled (Type 3 fonts) — not used for extraction.
     Captions combine PNG-crop OCR with full-page render OCR (same ZOOM as
     extract_ncert) via ncert_pdf_caption.merge_png_and_page_caption.
  2. _gather_multiline_caption:
       - blank_run threshold changed to 1 (was 2).
         NCERT captions have no blank lines between their own wrapped lines;
         the first blank line always means the caption has ended.
       - Removed subfig stop-check which was incorrectly halting collection
         when the same fig_id appeared without a letter suffix.
  3. ocr_caption_from_png tries PSM 6 then PSM 3 and uses whichever result
     actually contains the figure ID.
  4. Continuation lines are joined with a space (not newline) for a clean
     single-line caption string.
  5. normalize_page_caption applies the same “body bridge” trim as ncert_pdf_caption
     so merged PNG captions do not keep paragraphs like “The Mycoplasmas are…”;
     bridge patterns also catch OCR where the verb (“are”) is on the next line.

  6. Figures JSON is written after each figure (crash-safe partial output).

Run (any working directory):
  python3 path/to/backend/mm_rag/structure_fig.py
  python3 path/to/backend/mm_rag/structure_fig.py --no-ocr   # skip OCR, useful for testing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import ncert_pdf_caption as _npc
except ImportError:
    _npc = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Project root: …/backend/mm_rag → …/backend → repo root (for stable image_path strings).
REPO_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))

PDF_PATH = os.path.join(BASE_DIR, "books", "NCERT-Class-11-Biology.pdf")

# ── Config ────────────────────────────────────────────────────────────────────
INDEX_FILE = os.path.join(BASE_DIR, "outputs", "ncert_figures_v6", "index.txt")
IMAGES_DIR = os.path.join(BASE_DIR, "outputs", "ncert_figures_v6")
OUT_FILE = os.path.join(BASE_DIR, "outputs", "figures.json")

DEFAULT_BIOLOGY_CHAPTER_HEADINGS = [
    "The Living World",
    "Biological Classification",
    "Plant Kingdom",
    "Animal Kingdom",
    "Morphology of Flowering Plants",
    "Anatomy of Flowering Plants",
    "Structural Organisation in Animals",
    "Cell: The Unit of Life",
    "Biomolecules",
    "Cell Cycle and Cell Division",
    "Transport in Plants",
    "Mineral Nutrition",
    "Photosynthesis in Higher Plants",
    "Respiration in Plants",
    "Plant Growth and Development",
    "Digestion and Absorption",
    "Breathing and Exchange of Gases",
    "Body Fluids and Circulation",
    "Excretory Products and their Elimination",
    "Locomotion and Movement",
    "Neural Control and Coordination",
    "Chemical Coordination and Integration",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_index(index_file: str) -> list[dict]:
    records = []
    with open(index_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"Page\s+(\d+)\s+(\S+)\s+(\S+\.png)", line)
            if m:
                records.append({
                    "page":  int(m.group(1)),
                    "label": m.group(2),
                    "fname": m.group(3),
                })
    return records


def label_to_fig_id(label: str) -> str:
    m = re.match(r"Figure_(\d+)_(\d+)", label)
    return f"{m.group(1)}.{m.group(2)}" if m else label


def write_figures_json(out_path: str, figures: list[dict]) -> None:
    """Persist figures to JSON (called after each caption so progress is not lost on crash)."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(figures, f, indent=2, ensure_ascii=False)
        f.write("\n")


def chapter_from_label(label: str, chapter_headings: list[str] | None = None) -> str:
    """Map Figure_M_N to chapter title using 1-based index (or generic fallback)."""
    m = re.match(r"Figure_(\d+)_\d+", label)
    if not m:
        return "Unknown"
    idx = int(m.group(1))
    if chapter_headings and 1 <= idx <= len(chapter_headings):
        return chapter_headings[idx - 1]
    return f"Chapter {idx}"


def _looks_like_default_biology_pdf(pdf_path: str) -> bool:
    base = os.path.basename((pdf_path or "").lower())
    return base in {"ncert-class-11-biology.pdf", "english_biology_class-11.pdf"}


def _load_headings_file(path: str | None) -> list[str] | None:
    if not path:
        return None
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"Chapter headings file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError("Chapter headings file must be a JSON array of strings.")
    return data


def _norm_id(a: str, b: str) -> str:
    try:
        return f"{int(a)}.{int(b)}"
    except ValueError:
        return f"{a}.{b}"


def _fallback_caption_continuation_line(line: str, prev_line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if re.match(r"^\([a-z]\)\s+", s, flags=re.I):
        return True
    if re.match(r"^[a-z]\)\s+", s, flags=re.I):
        return True
    if re.match(r"^(and|or|with|while|as|in|of|for|to)\b", s, flags=re.I):
        return True
    if re.match(r"^[a-z]", s):
        return True
    if (prev_line or "").rstrip().endswith(":"):
        return True
    return False


caption_continuation_line = (
    getattr(_npc, "caption_continuation_line", None)
    or _fallback_caption_continuation_line
)


# Same-line / next-line body bleed as in ncert_pdf_caption (PNG crop has no column logic).
_CAPTION_BODY_BRIDGE = re.compile(
    r"(?i)(\.\s*However,|\s+However,|\s+These are\b|\s+Look at\b|"
    r"\s+The\s+[A-Za-z]{4,40}\s+(?:are|is|have|has|were|was|can|could|will|may)\b|"
    r"\s+The\s+[A-Za-z]{4,40}\s*$)"
)
# Next OCR line is a new textbook paragraph, not a wrapped caption line.
_NEXT_LINE_IS_BODY = re.compile(
    r"(?i)^(However,|Look at|Say,|Can you|These are|The\s+[A-Za-z]{4,40}\s+"
    r"(?:are|is|have|has|were|was|can|could|will|may)\b|"
    r"(?:are|is|have|has|were|was)\s+[a-z]|\d+\.\d+\s+KINGDOM\b)"
)
def _trim_caption_body_suffix(s: str) -> str:
    """Drop body text glued to the end of the 'Figure X.Y …' line."""
    s = s.strip()
    m = _CAPTION_BODY_BRIDGE.search(s)
    if m:
        return s[: m.start()].rstrip()
    return s


def _gather_multiline_caption(
    lines: list[str],
    start_row: int,
    first_fragment: str,
    fig_id: str,
    max_chars: int = 1200,
    max_extra: int = 12,
) -> str:
    """
    Collect wrapped caption lines starting after start_row.

    Stop conditions:
      - Blank line, unless the next non-empty line looks like a caption continuation
        (``(b) …``, ``Mosses – (c) …``, a wrapped ``gametophyte`` after ``Sphagnum``, etc.;
        see ``caption_continuation_line`` in ``ncert_pdf_caption``).
      - A line that starts a DIFFERENT figure's ID
      - max_extra continuation lines collected
      - accumulated length exceeds max_chars

    Returns all parts joined with a single space.
    """
    if "." in fig_id:
        a, b = fig_id.split(".", 1)
        fid_key = _norm_id(a, b)
    else:
        fid_key = fig_id

    new_fig_pat = re.compile(
        r"(?i)^\s*fig(?:ure|\.)?\s*[.:]?\s*(\d+)\s*[\.,]\s*(\d+)\b"
    )

    first_clean = _trim_caption_body_suffix(first_fragment.strip())
    parts = [first_clean]
    total_len = len(parts[0])

    j = start_row
    while j + 1 < len(lines):
        j += 1
        nxt = lines[j].strip()

        if not nxt:
            # One or more blank lines may sit between (a) / (b) / (c) blocks in the crop OCR.
            peek_k: int | None = None
            peek = ""
            for k in range(j + 1, len(lines)):
                t = lines[k].strip()
                if t:
                    peek_k, peek = k, t
                    break
            if peek and caption_continuation_line(peek, parts[-1]) and peek_k is not None:
                j = peek_k
                nxt = peek
            else:
                break

        if len(parts) - 1 >= max_extra:
            break

        if _NEXT_LINE_IS_BODY.match(nxt):
            break

        nm = new_fig_pat.match(nxt)
        if nm and _norm_id(nm.group(1), nm.group(2)) != fid_key:
            break  # start of a different figure's caption

        parts.append(nxt)
        total_len += 1 + len(nxt)

        if total_len >= max_chars:
            break

    return " ".join(parts)[:max_chars]


def _ocr_text(img_path: str, psm: int) -> str:
    try:
        img = Image.open(img_path).convert("RGB")
        return pytesseract.image_to_string(img, config=f"--psm {psm} --oem 1") or ""
    except Exception:
        return ""


def ocr_caption_from_png(abs_path: str, fig_id: str, max_chars: int = 1200) -> str:
    """
    Extract the full caption from the PNG crop using Tesseract OCR.

    Tries PSM 6 (uniform block) then PSM 3 (auto layout).
    Uses whichever result contains the figure ID.
    Falls back to last 4 non-empty lines of the crop if neither finds it.
    """
    if pytesseract is None or not os.path.isfile(abs_path):
        return ""

    # Build patterns to match "Figure X.Y" with OCR noise tolerance
    inline = re.compile(rf"(?i)fig(?:ure|\.)?\s*[.:]?\s*{re.escape(fig_id)}\b")

    parts = fig_id.split(".", 1)
    if len(parts) == 2:
        a2, b2 = re.escape(parts[0]), re.escape(parts[1])
        loose = re.compile(rf"(?i)fig(?:ure|\.)?.*?{a2}\s*[,.\s]\s*{b2}\b")
    else:
        loose = inline

    def _try_extract(txt: str) -> str | None:
        lines = txt.splitlines()

        for pattern in (inline, loose):
            best_col, best_row, best_line = 10**9, None, ""
            for i, raw in enumerate(lines):
                s = raw.strip()
                if not s:
                    continue
                m = pattern.search(s)
                if m and m.start() < best_col:
                    best_col, best_row, best_line = m.start(), i, s

            if best_row is not None:
                m = pattern.search(best_line)
                first = best_line[m.start():] if m else best_line
                return _gather_multiline_caption(lines, best_row, first, fig_id, max_chars)

        return None

    for psm in (6, 3):
        txt = _ocr_text(abs_path, psm)
        result = _try_extract(txt)
        if result:
            return result

    # Fallback: last 4 non-empty lines (caption is always at the bottom of the crop)
    txt_fallback = _ocr_text(abs_path, 6)
    nonempty = [ln.strip() for ln in txt_fallback.splitlines() if ln.strip()]
    return " ".join(nonempty[-4:])[:max_chars] if nonempty else ""


def _fallback_normalize_page_caption(caption: str, fig_id: str) -> str:
    _ = fig_id
    return _trim_caption_body_suffix(" ".join((caption or "").split()))


def _is_in_text_figure_reference_line(s: str, fig_id: str, match_start: int) -> bool:
    """
    True when 'Figure X.Y' is an in-text citation, e.g. '(Figure 2.2). The ...'
    or 'Figure 2.2). The cyanobacteria...' (OCR merges citation + paragraph).
    """
    s = s.strip()
    if not s:
        return True
    if 0 < match_start < len(s) and s[match_start - 1] == "(":
        return True
    frag = s[match_start:]
    return bool(
        re.match(
            rf"(?i)^fig(?:ure|\.)?\s*[.:]?\s*{re.escape(fig_id)}\s*\)\s*\.\s",
            frag.strip(),
        )
    )


def _fallback_merge_png_and_page_caption(png_cap: str, page_cap: str, fig_id: str) -> str:
    def _is_trivial(c: str) -> bool:
        s = " ".join((c or "").split()).strip()
        if not s:
            return True
        # Examples: "Figure 2.2)." / "Fig. 9.2."
        return bool(
            re.match(
                rf"(?i)^fig(?:ure|\.)?\s*[.:]?\s*{re.escape(fig_id)}\s*[\)\].,:;-]*$",
                s,
            )
        )

    fig_pat = re.compile(rf"(?i)\bfig(?:ure|\.)?\s*[.:]?\s*{re.escape(fig_id)}\b")

    # Page OCR often picks '(Figure 2.2). The ...' before the real caption line — drop it.
    page_use = page_cap
    if page_use and page_use.strip():
        m0 = fig_pat.search(page_use)
        if m0 and _is_in_text_figure_reference_line(page_use, fig_id, m0.start()):
            page_use = ""

    png_st = (png_cap or "").strip()
    page_st = (page_use or "").strip()

    # PNG crop is usually the real caption line; full-page OCR often merges columns
    # or continues into body text. If the first several words disagree, trust PNG.
    if png_st and not _is_trivial(png_st) and page_st:
        wp = re.findall(r"\S+", png_st.lower())
        wg = re.findall(r"\S+", page_st.lower())
        k = min(len(wp), len(wg), 8)
        if k >= 4 and wp[:k] != wg[:k]:
            return png_st

    cands = [c.strip() for c in (png_st, page_st) if c and c.strip()]
    if not cands:
        return ""
    with_id = [c for c in cands if fig_pat.search(c)]

    # Prefer candidates that contain the figure id and also carry actual content.
    non_trivial_with_id = [c for c in with_id if not _is_trivial(c)]
    if non_trivial_with_id:
        chosen = max(non_trivial_with_id, key=len)
    else:
        # If all id-matching candidates are trivial, prefer the richest available text.
        chosen = max(cands, key=len)

    return chosen


class _FallbackPageOcrCache:
    def __init__(self, pdf_doc):
        self.pdf_doc = pdf_doc
        self.cache: dict[int, str] = {}

    def _page_text(self, page_num: int) -> str:
        if page_num in self.cache:
            return self.cache[page_num]
        if pytesseract is None or fitz is None:
            self.cache[page_num] = ""
            return ""
        idx = max(0, page_num - 1)
        if idx >= len(self.pdf_doc):
            self.cache[page_num] = ""
            return ""
        page = self.pdf_doc[idx]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        txt = pytesseract.image_to_string(img, config="--psm 3 --oem 1") or ""
        self.cache[page_num] = txt
        return txt

    def caption(self, page_num: int, fig_id: str) -> str:
        txt = self._page_text(page_num)
        if not txt:
            return ""
        lines = txt.splitlines()
        fig_pat = re.compile(rf"(?i)fig(?:ure|\.)?\s*[.:]?\s*{re.escape(fig_id)}\b")
        for i, raw in enumerate(lines):
            s = raw.strip()
            if not s:
                continue
            m = fig_pat.search(s)
            if not m:
                continue
            if _is_in_text_figure_reference_line(s, fig_id, m.start()):
                continue
            first = s[m.start():] if m else s
            return _gather_multiline_caption(lines, i, first, fig_id)
        return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global INDEX_FILE, IMAGES_DIR, OUT_FILE, PDF_PATH
    ap = argparse.ArgumentParser(description="Structure NCERT figure metadata into figures.json")
    ap.add_argument("--no-ocr", action="store_true",
                    help="Skip Tesseract OCR (captions will be empty)")
    ap.add_argument("--no-pdf", action="store_true",
                    help="Skip full-page PDF OCR (PNG crop OCR only)")
    ap.add_argument(
        "--index-file",
        default=INDEX_FILE,
        help="Path to index.txt generated by extractor",
    )
    ap.add_argument(
        "--images-dir",
        default=IMAGES_DIR,
        help="Directory containing extracted figure PNGs",
    )
    ap.add_argument(
        "--out-file",
        default=OUT_FILE,
        help="Output figures.json path",
    )
    ap.add_argument(
        "--pdf",
        default=None,
        help="Path to NCERT PDF (default: books/NCERT-Class-11-Biology.pdf beside this script)",
    )
    ap.add_argument(
        "--chapter-headings-file",
        default=None,
        help="Optional JSON file (list[str]) to map Figure_<chapter>_* to chapter names",
    )
    args = ap.parse_args()

    INDEX_FILE = os.path.abspath(args.index_file)
    IMAGES_DIR = os.path.abspath(args.images_dir)
    OUT_FILE = os.path.abspath(args.out_file)
    if args.pdf:
        PDF_PATH = os.path.abspath(args.pdf)

    print("Week 2 — Multimodal Data Structuring", flush=True)
    print("=" * 50, flush=True)

    if not os.path.isfile(INDEX_FILE):
        print(f"ERROR: Index not found: {INDEX_FILE}", file=sys.stderr)
        sys.exit(1)

    if not args.no_ocr and pytesseract is None:
        print("WARNING: pytesseract not installed — captions will be empty.", file=sys.stderr)
        print("Install: pip install pytesseract  (+ system tesseract-ocr)", file=sys.stderr)

    index_records = parse_index(INDEX_FILE)
    print(f"Found {len(index_records)} figures in index.txt", flush=True)

    # Deferred import so --no-ocr still runs without optional deps on path.
    page_cache = None
    pdf_doc = None
    pdf_abs = os.path.abspath(args.pdf or PDF_PATH)
    chapter_headings = _load_headings_file(args.chapter_headings_file)
    if chapter_headings is None and _looks_like_default_biology_pdf(pdf_abs):
        chapter_headings = DEFAULT_BIOLOGY_CHAPTER_HEADINGS
    merge_png_and_page_caption = (
        getattr(_npc, "merge_png_and_page_caption", None) or _fallback_merge_png_and_page_caption
    )
    normalize_page_caption = (
        getattr(_npc, "normalize_page_caption", None) or _fallback_normalize_page_caption
    )
    if (
        not args.no_ocr
        and not args.no_pdf
        and fitz is not None
        and os.path.isfile(pdf_abs)
    ):
        pdf_doc = fitz.open(pdf_abs)
        page_cache_cls = getattr(_npc, "PageOcrCache", None) or _FallbackPageOcrCache
        page_cache = page_cache_cls(pdf_doc)
        print(f"Full-page OCR cache: {pdf_abs}", flush=True)
    elif not args.no_ocr and not args.no_pdf:
        if not os.path.isfile(pdf_abs):
            print(f"WARNING: PDF not found ({pdf_abs}) — PNG OCR only.", file=sys.stderr)
        if fitz is None:
            print("WARNING: PyMuPDF (fitz) not installed — PNG OCR only.", file=sys.stderr)

    figures = []
    ocr_used = 0

    try:
        for rec in index_records:
            page = rec["page"]
            label = rec["label"]
            fname = rec["fname"]
            fig_id = label_to_fig_id(label)
            chapter = chapter_from_label(label, chapter_headings=chapter_headings)
            abs_img = os.path.join(IMAGES_DIR, fname)
            # Repo-relative path (matches existing figures.json / build_multimodal_index expectations).
            image_path = os.path.relpath(abs_img, REPO_ROOT).replace(os.sep, "/")
            caption = ""

            if not args.no_ocr and os.path.isfile(abs_img):
                png_cap = ocr_caption_from_png(abs_img, fig_id)
                if page_cache is not None:
                    page_cap = page_cache.caption(page, fig_id)
                    caption = normalize_page_caption(
                        merge_png_and_page_caption(png_cap, page_cap, fig_id),
                        fig_id,
                    )
                else:
                    caption = (
                        normalize_page_caption(png_cap, fig_id)
                        if normalize_page_caption
                        else png_cap
                    )
                if caption:
                    ocr_used += 1

            figures.append({
                "fig_id": fig_id,
                "label": label,
                "page": page,
                "chapter": chapter,
                "caption": caption,
                "image_path": image_path,
            })
            write_figures_json(OUT_FILE, figures)

            ch_short = chapter[:36] + ("…" if len(chapter) > 36 else "")
            cap_hint = (caption[:60] + "…") if len(caption) > 60 else (caption or "(no caption)")
            print(f"  ✓ {label:<24}  {ch_short:<28}  {cap_hint}", flush=True)
    finally:
        if pdf_doc is not None:
            pdf_doc.close()

    if len(index_records) == 0:
        write_figures_json(OUT_FILE, figures)

    print(f"\n{'=' * 50}", flush=True)
    print(f"Total records structured : {len(figures)}", flush=True)
    if not args.no_ocr:
        print(f"Captions found via OCR   : {ocr_used}", flush=True)
    print(f"Output saved to          : {OUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
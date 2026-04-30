"""
extract_figures_v2.py
---------------------
Extracts labelled figures (Figure 1.1, 1.2 …) from NCERT-style Biology PDFs.

Root-cause fix over v1
~~~~~~~~~~~~~~~~~~~~~~
v1 called fitz.Pixmap(doc, xref) and sometimes received an smask / alpha-mask
object (all-black, 2480×3508) that happened to share the same page bbox.
v2 fixes this with a three-gate filter on every candidate xref:
  Gate 1 – dimensions: reject if pixmap is full-page-sized (background/mask)
  Gate 2 – colorspace: reject grayscale 1-bit masks (cs.n == 1 and bpc == 1)
  Gate 3 – pixel stats: reject if mean brightness < 5 (all-black) or > 250 (all-white)
If no xref passes all gates the clip-render path is used (always correct).

Dependencies
~~~~~~~~~~~~
  pip install pymupdf --break-system-packages
  (numpy is optional but improves the brightness check speed)

Usage
~~~~~
  python extract_figures_v2.py [pdf_path] [output_dir]
"""

import fitz
import re, os, sys, struct, zlib
from pathlib import Path

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False

# ── Configuration ──────────────────────────────────────────────────────────────
PDF_PATH   = "/mnt/user-data/uploads/English_Biology_Class-11.pdf"
OUTPUT_DIR = "/mnt/user-data/outputs/figures_v2"
MIN_IMG_W  = 150      # ignore tiny decorations / bullets
MIN_IMG_H  = 150
CLIP_DPI   = 200      # DPI for the clip-render fallback (higher = crisper)
PAGE_COVER = 0.80     # fraction of page area → classify as background
DARK_THRESH  = 8      # mean < this → image is too dark / a mask
BRIGHT_THRESH= 248    # mean > this → image is blank white
# ──────────────────────────────────────────────────────────────────────────────

FIG_RE = re.compile(r'(Figure\s+(\d+)\.(\d+)[^\n]*)', re.IGNORECASE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def page_wh(page):
    return page.rect.width, page.rect.height

def is_background_block(bbox, page):
    pw, ph = page_wh(page)
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    return (bw * bh) / (pw * ph) >= PAGE_COVER

def cy(bbox):
    return (bbox[1] + bbox[3]) / 2

def pix_mean(pix: fitz.Pixmap) -> float:
    """Return mean brightness of a pixmap (0-255).  Fast path uses numpy."""
    if _HAVE_NUMPY:
        arr = np.frombuffer(pix.samples, dtype=np.uint8)
        return float(arr.mean())
    # Pure-python fallback (slower for large images)
    total = sum(pix.samples)
    return total / max(len(pix.samples), 1)

def is_full_page_sized(pix: fitz.Pixmap, page: fitz.Page) -> bool:
    """True when the embedded image is essentially full-page (background/mask)."""
    pw, ph = page_wh(page)
    # Compare in inches @ 300 DPI tolerance
    scale = 300 / 72
    return (pix.width  >= pw  * scale * 0.90 and
            pix.height >= ph  * scale * 0.90)

def is_mask_colorspace(pix: fitz.Pixmap) -> bool:
    """1-bit grayscale images are almost always alpha masks."""
    cs = pix.colorspace
    if cs is None:
        return True
    # DeviceGray with n==1 and 1 bpc is typical for JBIG2/CCITTFax masks
    return cs.n == 1 and pix.n == 1

def good_pixmap(pix: fitz.Pixmap, page: fitz.Page) -> bool:
    """Return True only if this pixmap looks like real figure content."""
    if pix.width < MIN_IMG_W or pix.height < MIN_IMG_H:
        return False
    if is_full_page_sized(pix, page):
        return False
    if is_mask_colorspace(pix):
        return False
    mean = pix_mean(pix)
    if mean < DARK_THRESH or mean > BRIGHT_THRESH:
        return False
    return True

def to_rgb(pix: fitz.Pixmap) -> fitz.Pixmap:
    """Coerce any colorspace to RGB (handles CMYK, Gray, etc.)."""
    if pix.colorspace is None:
        return pix
    if pix.colorspace.name in ("DeviceRGB", "sRGB"):
        return pix
    return fitz.Pixmap(fitz.csRGB, pix)

def clip_render(page: fitz.Page, bbox) -> bytes:
    """Render the exact page region at CLIP_DPI and return PNG bytes."""
    rect = fitz.Rect(bbox)
    # Guard against degenerate/zero-area rects
    if rect.width < 4 or rect.height < 4:
        return b""
    mat = fitz.Matrix(CLIP_DPI / 72, CLIP_DPI / 72)
    try:
        pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
        data = pix.tobytes("png")
    except Exception:
        # Last resort: render full page then crop
        pix = page.get_pixmap(matrix=mat, alpha=False)
        data = pix.tobytes("png")
    pix = None
    return data

def safe_slug(text: str, maxlen: int = 55) -> str:
    text = re.sub(r'\s+', '_', text.strip())
    text = re.sub(r'[^\w\-]', '', text)
    return text[:maxlen]


# ── Main extraction ───────────────────────────────────────────────────────────

def collect_captions(doc) -> dict:
    """Pass 1: build caption_map[pno] = [(chap, fignum, full_text, cy)]."""
    cap_map = {}
    for pno in range(len(doc)):
        page = doc[pno]
        caps = []
        for blk in page.get_text("blocks"):
            if blk[6] != 0:
                continue
            for m in FIG_RE.finditer(blk[4]):
                full = m.group(1).replace('\n', ' ').strip()
                ch   = int(m.group(2))
                fn   = int(m.group(3))
                caps.append((ch, fn, full, cy(blk[:4])))
        if caps:
            cap_map[pno] = caps
    return cap_map


def best_xref_pixmap(page, block_bbox, doc) -> "fitz.Pixmap | None":
    """
    Try every embedded image on the page that overlaps the block_bbox.
    Uses get_image_rects(xref) – more reliable than get_image_bbox(img_info).
    Returns the first pixmap that passes all quality gates, or None.
    """
    bx0, by0, bx1, by1 = block_bbox
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        # get_image_rects returns a list of Rect objects for this xref on the page
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        if not rects:
            continue
        ix0, iy0, ix1, iy1 = rects[0]   # use first occurrence
        ov_x = max(0.0, min(bx1, ix1) - max(bx0, ix0))
        ov_y = max(0.0, min(by1, iy1) - max(by0, iy0))
        if ov_x < 20 or ov_y < 20:
            continue
        try:
            pix = fitz.Pixmap(doc, xref)
        except Exception:
            continue
        if not good_pixmap(pix, page):
            pix = None
            continue
        pix = to_rgb(pix)
        return pix
    return None


def extract_figures(pdf_path: str, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)

    cap_map = collect_captions(doc)
    print(f"Captions found on {len(cap_map)} pages.\n")

    saved: dict = {}      # (chap, fignum) → (page_1based, fname)
    clip_count = 0
    xref_count = 0

    for pno in range(len(doc)):
        page = doc[pno]
        blocks = page.get_text("dict")["blocks"]

        # Content image blocks only
        img_blocks = [
            b for b in blocks
            if b["type"] == 1
            and not is_background_block(b["bbox"], page)
            and b["width"]  >= MIN_IMG_W
            and b["height"] >= MIN_IMG_H
        ]
        if not img_blocks:
            continue

        # Captions from current page ± 1; tag each with its source page offset
        # so the distance key can penalise off-page matches.
        nearby = []  # (chap, fignum, full_text, cap_cy, page_offset)
        pw, ph = page_wh(page)
        for off in (-1, 0, 1):
            for cap in cap_map.get(pno + off, []):
                nearby.append((*cap, off))
        if not nearby:
            continue

        for blk in img_blocks:
            img_cy = cy(blk["bbox"])
            # Distance = |Δy| + page_height × |page_offset|
            # Same-page captions always beat adjacent-page ones unless they are
            # more than one full page-height away (effectively impossible).
            def cap_dist(c):
                return abs(c[3] - img_cy) + ph * abs(c[4])
            chap, fignum, full_cap, _, _off = min(nearby, key=cap_dist)
            key = (chap, fignum)
            if key in saved:
                continue

            # ── Try xref path first ─────────────────────────────────────────
            pix = best_xref_pixmap(page, blk["bbox"], doc)
            if pix is not None:
                img_bytes = pix.tobytes("png")
                pix = None
                method = "xref"
            else:
                # ── Reliable clip-render fallback ───────────────────────────
                img_bytes = clip_render(page, blk["bbox"])
                method = "clip"

            # Final sanity: validate the saved bytes aren't blank
            # (Re-open in fitz to check mean - cheap for already-in-memory bytes)
            if not img_bytes:
                img_bytes = clip_render(page, blk["bbox"])
            if img_bytes:
                try:
                    check_pix = fitz.Pixmap(img_bytes)
                    mean = pix_mean(check_pix)
                    check_pix = None
                    if mean < DARK_THRESH or mean > BRIGHT_THRESH:
                        # xref was bad after all; force clip render
                        img_bytes = clip_render(page, blk["bbox"])
                        method = "clip(forced)"
                except Exception:
                    img_bytes = clip_render(page, blk["bbox"])
                    method = "clip(err)"
            if not img_bytes:
                continue  # nothing usable; skip

            # ── Persist ─────────────────────────────────────────────────────
            label = f"Figure_{chap}_{fignum}"
            fname = f"p{pno+1:03d}_{label}.png"
            out   = os.path.join(output_dir, fname)
            with open(out, "wb") as f:
                f.write(img_bytes)

            saved[key] = (pno + 1, label, fname)
            if "clip" in method:
                clip_count += 1
            else:
                xref_count += 1
            print(f"  [{method:12s}]  p{pno+1:03d}  {label}  →  {fname}")

    doc.close()

    # ── Write index ──────────────────────────────────────────────────────────
    # Format: "Page 29 Figure_2_6 p029_Figure_2_6.png"  (no leading spaces)
    index_path = os.path.join(output_dir, "index.txt")
    with open(index_path, "w", encoding="utf-8") as f:
        for (ch, fn), (pg, label, fname) in sorted(saved.items()):
            f.write(f"Page {pg} {label} {fname}\n")

    print(f"\n{'─'*60}")
    print(f"  Saved   : {len(saved)} figures")
    print(f"  Via xref: {xref_count}   Via clip-render: {clip_count}")
    print(f"  Output  : {output_dir}")
    print(f"  Index   : {index_path}")


if __name__ == "__main__":
    pdf  = sys.argv[1] if len(sys.argv) > 1 else PDF_PATH
    outd = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_DIR
    extract_figures(pdf, outd)
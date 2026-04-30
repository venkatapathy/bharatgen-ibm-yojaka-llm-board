"""
NCERT Biology Figure Extractor v6 — Complete Final
====================================================
Strategy: CAPTION-FIRST + COLUMN-AWARE + CONTENT-EXTENT TRACKING

All bugs fixed:
  1. contours_in filter: broken operator precedence fixed
  2. Right-column figures: find LEFT boundary via right-to-left density scan
  3. Stacked figures: track content extent (not just caption bottom) per column
     so OCR-missed sub-captions like "Figure 4.1 (b)" don't pollute Fig 4.2's crop
  4. False positives: ")" in number token → parenthetical ref, skip
"""

import argparse
import fitz
import pytesseract
import cv2
import numpy as np
import re
import os
from PIL import Image
from scipy.ndimage import uniform_filter1d

# ── configuration (paths relative to this file; works from any CWD) ───────────
_BASE = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.path.join(_BASE, "books", "NCERT-Class-11-Biology.pdf")
OUT_DIR = os.path.join(_BASE, "outputs", "ncert_figures_v6")
os.makedirs(OUT_DIR, exist_ok=True)

ZOOM                = 200 / 72.0
PAGE_HEADER         = 0.12
MAX_DECOR_W         = 0.85
MIN_FIG_AREA        = 0.005
DILATE_K            = 15
DILATE_ITER         = 3
BLANK_ROWS          = 5
# Wrapped NCERT captions (narrow column): require this many px below cap_y
# before a blank-row gap can end the caption — avoids stopping after line 1.
CAPTION_GAP_MIN_DEPTH = 115
RIGHT_COL_THRESHOLD = 0.45
MIN_COL_GAP         = 40    # px — blank gap to separate stacked figures in same col
MAX_RIGHT_EXTEND_BELOW_CAPTION = 380  # px cap for stacked right-column figures
MAX_RIGHT_EXTEND_WITH_NEXT_CAP = 220  # tighter cap when next caption exists
TEXT_HEAVY_TEXT_COV = 0.30
TEXT_HEAVY_NON_TEXT_INK = 0.010

BODY_REF_WORDS = {
    "in","see","shown","from","at","of","refer","as","the","a",
    "using","by","on","to","with",
}

# Extracted figure filenames: pNNN_Figure_X_Y.png
_FIGURE_FNAME_RE = re.compile(r"^p(\d+)_(Figure_\d+_\d+)\.png$", re.I)

CAPTION_CUE_WORDS = {
    "diagrammatic",
    "broad",
    "classification",
    "showing",
    "examples",
    "example",
    "sectional",
    "view",
    "experiment",
    "cyclic",
    "dominance",
    "phyllotaxy",
    "types",
}


# ── helpers ───────────────────────────────────────────────────────────────────
def _safe_conf(v):
    """Tesseract conf can be int/float string or -1."""
    try:
        return float(v)
    except Exception:
        return -1.0


def _normalize_fig_text(s):
    """Normalize OCR noise around figure IDs like '5 . 1(a)'."""
    s = (s or "").replace(",", ".")
    s = re.sub(r"\s+", "", s)
    return s


def _parse_fig_id_from_tokens(tokens):
    """
    Parse figure id from a short token window.
    Accepts variants like:
      5.1, 5 . 1, 5,1, 5.1(a), (5.1), 5.1).
    """
    joined = _normalize_fig_text(" ".join(tokens))
    m = re.search(r"(\d+)\.(\d+)", joined)
    if not m:
        return None, joined
    return f"{m.group(1)}.{m.group(2)}", joined


def _has_caption_cues(lookahead_tokens, norm_tail):
    """
    OCR may merge caption with a previous sentence. In that case, rely on
    common caption cue words/patterns after figure id.
    """
    low_tokens = [t.lower() for t in lookahead_tokens if t]
    if any(t in CAPTION_CUE_WORDS for t in low_tokens):
        return True
    if ":" in norm_tail:
        return True
    if "(a)" in norm_tail.lower() or "(b)" in norm_tail.lower() or "(c)" in norm_tail.lower():
        return True
    return False


def _page_has_figure_ref(page):
    """
    Robust page gate:
    1) PDF text extraction (fast and accurate when text layer exists)
    2) Low-res OCR fallback with two PSMs
    """
    pat = r"Fig(?:ure|\.?)[\s.]*\d+\s*[.,]?\s*\d+"

    pdf_text = page.get_text("text") or ""
    if re.search(pat, pdf_text, re.I):
        return True

    pix_lo = page.get_pixmap(
        matrix=fitz.Matrix(100 / 72, 100 / 72), colorspace=fitz.csRGB
    )
    arr_lo = np.frombuffer(pix_lo.samples, np.uint8).reshape(
        pix_lo.height, pix_lo.width, 3
    )
    pil = Image.fromarray(arr_lo[..., ::-1])

    txt_3 = pytesseract.image_to_string(pil, config="--psm 3 --oem 1")
    if re.search(pat, txt_3, re.I):
        return True

    txt_6 = pytesseract.image_to_string(pil, config="--psm 6 --oem 1")
    return bool(re.search(pat, txt_6, re.I))


def _ocr_caption_passes(img_bgr):
    """Yield OCR token dicts from complementary caption-detection passes."""
    pil_rgb = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    yield pytesseract.image_to_data(
        pil_rgb, output_type=pytesseract.Output.DICT, config="--psm 3 --oem 1"
    )

    # Binarized pass improves recall on faint/small caption text.
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12
    )
    pil_bw = Image.fromarray(bw)
    yield pytesseract.image_to_data(
        pil_bw, output_type=pytesseract.Output.DICT, config="--psm 6 --oem 1"
    )


def _line_key(data, idx):
    return (
        data.get("block_num", [0])[idx],
        data.get("par_num", [0])[idx],
        data.get("line_num", [0])[idx],
    )


def _line_indices(data, idx):
    key = _line_key(data, idx)
    n = len(data["text"])
    out = []
    for j in range(n):
        t = data["text"][j].strip()
        if not t:
            continue
        if _line_key(data, j) == key and _safe_conf(data["conf"][j]) > 10:
            out.append(j)
    return out


def _is_likely_caption_anchor(data, idx):
    """
    True captions usually start at/near the beginning of a line.
    Body references like "... Figure 4.2b)." are typically mid-line.
    """
    line_idxs = _line_indices(data, idx)
    if not line_idxs:
        return False
    try:
        pos = line_idxs.index(idx)
    except ValueError:
        return False
    return pos <= 1


def _norm_same_line_from_idx(data, idx):
    """
    Concatenate OCR tokens from idx through the end of the same line, then
    normalize. Used to detect body refs like '(Figure 8.4).' when ')' is
    tokenized several words after the figure id (short lookahead misses it).
    """
    line_idxs = _line_indices(data, idx)
    if not line_idxs:
        return _normalize_fig_text((data["text"][idx] or "").strip())
    try:
        pos = line_idxs.index(idx)
    except ValueError:
        return _normalize_fig_text((data["text"][idx] or "").strip())
    parts = []
    for j in line_idxs[pos:]:
        tok = (data["text"][j] or "").strip()
        if tok:
            parts.append(tok)
    return _normalize_fig_text(" ".join(parts))


def _text_mask_from_ocr(roi_bgr, conf_thresh=35):
    """
    Build a text mask for a ROI using OCR word boxes.
    Masked pixels are likely body/caption text and should not drive contour bbox.
    """
    H, W = roi_bgr.shape[:2]
    if H < 30 or W < 30:
        return np.zeros((H, W), dtype=np.uint8)
    try:
        pil = Image.fromarray(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB))
        data = pytesseract.image_to_data(
            pil, output_type=pytesseract.Output.DICT, config="--psm 6 --oem 1"
        )
    except Exception:
        return np.zeros((H, W), dtype=np.uint8)

    mask = np.zeros((H, W), dtype=np.uint8)
    n = len(data.get("text", []))
    roi_area = float(H * W)
    for i in range(n):
        tok = (data["text"][i] or "").strip()
        conf = _safe_conf(data["conf"][i])
        if conf < conf_thresh or len(tok) < 2:
            continue
        x = int(data["left"][i]); y = int(data["top"][i])
        w = int(data["width"][i]); h = int(data["height"][i])
        if w <= 0 or h <= 0:
            continue
        # Keep mask conservative: avoid large OCR boxes that can swallow diagrams.
        box_area = w * h
        if box_area > 0.012 * roi_area:
            continue
        if h > 0.12 * H or w > 0.65 * W:
            continue
        pad_x = max(2, int(0.08 * w))
        pad_y = max(2, int(0.20 * h))
        x1 = max(0, x - pad_x); y1 = max(0, y - pad_y)
        x2 = min(W, x + w + pad_x); y2 = min(H, y + h + pad_y)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def _detect_caption_id_in_crop(crop_bgr):
    """
    Detect first Figure X.Y mention inside a crop.
    Returns 'X.Y' or None.
    """
    try:
        txt = pytesseract.image_to_string(
            Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)),
            config="--psm 6 --oem 1"
        )
    except Exception:
        return None
    m = re.search(r"Fig(?:ure|\.?)\s*([0-9]+\.[0-9]+)", txt, re.I)
    return m.group(1) if m else None


def _all_caption_ids_in_crop(crop_bgr):
    try:
        txt = pytesseract.image_to_string(
            Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)),
            config="--psm 6 --oem 1"
        )
    except Exception:
        return set()
    return set(re.findall(r"Fig(?:ure|\.?)\s*([0-9]+\.[0-9]+)", txt, re.I))


def _crop_quality_metrics(crop_bgr):
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    tmask = _text_mask_from_ocr(crop_bgr, conf_thresh=35)
    text_cov = float(np.mean(tmask > 0))
    non_text_ink = float(np.mean((gray < 220) & (tmask == 0)))
    # Higher is better: prefer graphic-heavy and penalize text-heavy crops.
    score = non_text_ink - 0.35 * text_cov
    return score, text_cov, non_text_ink


def render(page):
    pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def caption_bottom_by_gap(
    img_bgr,
    cap_y,
    cap_x=None,
    is_right_col=False,
    max_scan=420,
    min_depth_before_gap=None,
):
    """
    Return y of first run of BLANK_ROWS consecutive 'empty' rows after cap_y.

    Rows are 'empty' only within a horizontal band around the caption. Using
    full image width, narrow wrapped captions can leave many full-width rows
    with no ink while the next caption line sits in a small x-range — five
    such rows falsely end the caption after the first line (e.g. Figure 1.1).

    Until cap_y + min_depth_before_gap, gap detection is disabled so wrapped
    lines are included.
    """
    H, W = img_bgr.shape[:2]
    if min_depth_before_gap is None:
        min_depth_before_gap = CAPTION_GAP_MIN_DEPTH

    if cap_x is not None:
        cx = int(cap_x)
        if is_right_col:
            x0 = max(0, cx - 70)
            x1 = W
        else:
            x0 = max(0, cx - 90)
            x1 = min(W, int(W * RIGHT_COL_THRESHOLD) + 20)
        if x1 <= x0:
            x0, x1 = 0, W
    else:
        x0, x1 = 0, W

    min_stop_y = min(H, cap_y + min_depth_before_gap)

    def band_rows_all_empty(y0: int) -> bool:
        if y0 + BLANK_ROWS > H:
            return True
        win = img_bgr[y0 : y0 + BLANK_ROWS, x0:x1, :]
        for i in range(BLANK_ROWS):
            if np.any(win[i] < 220):
                return False
        return True

    for y in range(cap_y, min(H, cap_y + max_scan)):
        if y < min_stop_y:
            continue
        if band_rows_all_empty(y):
            return y
    return min(H, cap_y + max_scan)


def content_extent_in_column(img_bgr, from_y, to_y, col_left, col_right):
    """
    Scan downward from from_y to to_y in x[col_left:col_right].
    Find the first blank gap >= MIN_COL_GAP rows; return the y where
    the gap ENDS (= where next content begins), or to_y if no gap found.
    This detects the true bottom of a figure even when a sub-caption
    (e.g. "Figure 4.1 (b)") was missed by the main OCR pass.
    """
    H, W     = img_bgr.shape[:2]
    col_left  = max(0, col_left)
    col_right = min(W, col_right)
    from_y    = max(0, from_y)
    to_y      = min(H, to_y)

    if to_y <= from_y or col_right <= col_left:
        return to_y

    roi  = img_bgr[from_y:to_y, col_left:col_right]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    row_has_content = np.any(gray < 220, axis=1)

    gap_start = None
    gap_len   = 0
    for y, has in enumerate(row_has_content):
        if not has:
            if gap_start is None:
                gap_start = y
            gap_len += 1
        else:
            if gap_len >= MIN_COL_GAP:
                # Gap ends at y — return from_y + y as the start of next content
                return from_y + y
            gap_start = None
            gap_len   = 0

    return to_y   # no separating gap found


def find_all_captions(img_bgr):
    """
    OCR full page → list of caption dicts sorted by y:
      { fig_id, cap_x, cap_y, cap_y2, is_right_col }

    Exclusions:
      1. Preceding word is a body-ref preposition / article
      2. ")" appears in the number token itself (parenthetical reference)
    Keeps the LOWEST-y (furthest down on page) occurrence per fig_id.
    """
    H, W = img_bgr.shape[:2]
    by_id = {}

    for data in _ocr_caption_passes(img_bgr):
        n = len(data["text"])
        for i, txt in enumerate(data["text"]):
            raw_t = txt.strip()
            if not raw_t or _safe_conf(data["conf"][i]) < 20:
                continue
            # Normalize leading punctuation variants like "(Figure".
            t = re.sub(r"^[^A-Za-z]+", "", raw_t)

            fig_id = None
            norm_tail = ""
            lookahead = []

            # Case A: merged token like "Figure15.7" / "Fig.15.4".
            merged = re.search(r"^Fig(?:ure|\.)?\s*(\d+\.\d+)", t, re.I)
            if merged and not re.fullmatch(r"Fig(ure|\.)?", t, re.I):
                fig_id = merged.group(1)
                lookahead = [data["text"][k].strip() for k in range(i + 1, min(n, i + 5))]
                norm_tail = _normalize_fig_text(t + " " + " ".join(lookahead))
            else:
                # Case B: split tokens "Figure" + "15.7".
                if not re.match(r"^Fig(ure|\.)?$", t, re.I):
                    continue
                if i + 1 >= n:
                    continue
                lookahead = [data["text"][k].strip() for k in range(i + 1, min(n, i + 5))]
                fig_id, norm_tail = _parse_fig_id_from_tokens(lookahead)
                if not fig_id:
                    continue

            cx, cy = data["left"][i], data["top"][i]
            is_anchor = _is_likely_caption_anchor(data, i)
            has_cue = _has_caption_cues(lookahead, norm_tail)

            # Rule 1: preceding word is a body-ref word
            prev_word = ""
            for j in range(i - 1, max(-1, i - 5), -1):
                pw = data["text"][j].strip()
                if pw and _safe_conf(data["conf"][j]) > 20:
                    prev_word = pw.lower().rstrip(".,;:()")
                    break
            # Do not reject true caption anchors just because OCR reports a
            # preceding body-ref token (common on wrapped/scanned lines).
            if prev_word in BODY_REF_WORDS and not (is_anchor or has_cue):
                continue

            # Rule 2 (relaxed): if ')' appears before number token, likely body ref.
            close_idx = norm_tail.find(")")
            dot_idx = norm_tail.find(".")
            if close_idx != -1 and (dot_idx == -1 or close_idx < dot_idx):
                continue

            # Rule 3: inline body-reference style "4.2b)." (without "(b)") is not a caption.
            if re.search(r"^\d+\.\d+[a-z]\)", norm_tail, re.I) and "(" not in norm_tail:
                continue
            # Also reject variants like "4.3b." / "4.3b);" captured from body text.
            first_tok_norm = _normalize_fig_text(lookahead[0]) if lookahead else ""
            if re.match(r"^\d+\.\d+[a-z]", first_tok_norm, re.I):
                continue

            # Rule 3b: body sentences like "... model (Figure 8.4)." — id followed by ')'
            # on the same OCR line. Use full line (not 4-token lookahead) so a separate
            # ')' token is still caught. Real captions put a title after the id, not ')'.
            id_esc = re.escape(fig_id.replace(",", "."))
            line_norm = _norm_same_line_from_idx(data, i)
            if re.search(rf"(?i)Figure{id_esc}\)", line_norm):
                continue

            # Rule 4: caption anchors are expected near the start of line.
            if not (is_anchor or has_cue):
                continue

            is_right_col = cx / W > RIGHT_COL_THRESHOLD
            cap_y2 = caption_bottom_by_gap(
                img_bgr, cy, cap_x=cx, is_right_col=is_right_col
            )

            entry = {
                "fig_id": fig_id,
                "cap_x": cx,
                "cap_y": cy,
                "cap_y2": cap_y2,
                "is_right_col": is_right_col,
                "_anchor_score": 1,
            }
            old = by_id.get(fig_id)
            if old is None:
                by_id[fig_id] = entry
            elif entry["_anchor_score"] > old.get("_anchor_score", 0):
                by_id[fig_id] = entry
            elif entry["_anchor_score"] == old.get("_anchor_score", 0) and cy > old["cap_y"]:
                by_id[fig_id] = entry

    for v in by_id.values():
        v.pop("_anchor_score", None)
    return sorted(by_id.values(), key=lambda c: c["cap_y"])


def _find_column_gap(sm, W, scan_start, scan_end, threshold=0.06, min_width=25):
    """
    Shared helper: find the widest gap (density < threshold, width >= min_width px)
    in sm[scan_start:scan_end].  Returns (gap_start, gap_end) or None.
    """
    best = None
    gs, gl = None, 0
    for x in range(max(0, scan_start), min(W, scan_end)):
        if sm[x] < threshold:
            if gs is None: gs = x
            gl += 1
        else:
            if gl >= min_width:
                if best is None or gl > best[2]:
                    best = (gs, gs + gl, gl)
            gs, gl = None, 0
    if gl >= min_width and gs is not None:
        if best is None or gl > best[2]:
            best = (gs, gs + gl, gl)
    return best


def _has_top_band_content(img_bgr, x, W, y_start=0.13, y_end=0.18,
                           threshold=0.02, half_width=15):
    """
    Returns True if column x has content in the very top band y[13%-18%].
    Body text in a two-column layout appears here from the first line.
    Figure panels that are side-by-side start lower on the page, so are
    absent in this band — this distinguishes them from real column separators.
    """
    H, _ = img_bgr.shape[:2]
    band  = img_bgr[int(H*y_start):int(H*y_end),
                    max(0, x-half_width):min(W, x+half_width)]
    if band.size == 0:
        return False
    return float(np.mean(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) < 220)) > threshold


def find_right_col_left_boundary(img_bgr, cap_x, search_y1, search_y2):
    """
    Right-column figure: find the left boundary of the figure column.
    Widest white trough in x[cap_x-600:cap_x+50] where the right side
    (a) has density > 0.04 and (b) has body-text in the top band y[13-18%].
    Condition (b) eliminates false positives from figure-internal gaps.
    Returns gap end, or 0 if no real separator found.
    """
    H, W  = img_bgr.shape[:2]
    zone  = img_bgr[search_y1:search_y2, :, :]
    gray  = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    sm    = uniform_filter1d(np.mean(gray < 220, axis=0).astype(float), size=20)

    gap = _find_column_gap(sm, W, cap_x - 600, cap_x + 50,
                           threshold=0.06, min_width=6)   # narrow gaps OK for right-col
    if not gap:
        return 0
    _, gend, _ = gap
    right_d = np.mean(sm[gend: min(W, gend + 300)])
    if right_d <= 0.04:
        return 0
    return gend if _has_top_band_content(img_bgr, gend + 20, W) else 0


def _is_fullwidth_figure(pdf_page):
    """
    Returns True if the PDF page has figure content in BOTH the left half
    (x<55%) and the right half (x>45%), indicating a full-page-width figure.
    Checks embedded raster images first, then vector drawings as fallback.
    Used to prevent find_left_col_right_boundary from splitting multi-panel
    figures that span both columns (e.g. Fig 3.2 Bryophytes 4-panel layout).
    """
    if pdf_page is None:
        return False
    pw, ph = pdf_page.rect.width, pdf_page.rect.height

    # Check embedded raster images (any coverage > 0.02% — catches tiny cells)
    has_left = has_right = False
    for img_item in pdf_page.get_images(full=True):
        try:
            for r in pdf_page.get_image_rects(img_item[0]):
                cov = (r.width * r.height) / (pw * ph)
                if cov > 0.0002 and r.y0 / ph > 0.10 and r.y1 / ph < 0.92:
                    if r.x1 / pw < 0.55:
                        has_left = True
                    if r.x0 / pw > 0.45:
                        has_right = True
        except Exception:
            pass

    if has_left and has_right:
        return True

    # Fallback: check vector drawings
    has_left = has_right = False
    for d in pdf_page.get_drawings():
        r   = d["rect"]
        cov = (r.width * r.height) / (pw * ph)
        if cov > 0.005 and r.y0 / ph > 0.10 and r.y1 / ph < 0.92:
            if r.x1 / pw < 0.55:
                has_left = True
            if r.x0 / pw > 0.45:
                has_right = True

    return has_left and has_right


def find_left_col_right_boundary(img_bgr, cap_x, search_y1, search_y2,
                                  pdf_page=None):
    """
    Left-column figure: find the right boundary of the figure column.
    Widest white trough in x[cap_x+50:cap_x+700] where the right side
    (a) has density > 0.04 and (b) has body-text in the top band y[13-18%].
    Condition (b) eliminates false positives where figure panels are
    side-by-side (e.g. Fig 2.6 TMV+Bacteriophage) — their gap lacks
    body text at the very top of the page.

    Also uses PDF image/drawing metadata: if the page has significant
    content in BOTH left AND right halves (full-width figure like Fig 3.2),
    returns W immediately without gap detection.

    Returns gap end, or W if no real separator found (full-width figure).
    """
    # Primary: use PDF metadata to detect true full-width figures
    if _is_fullwidth_figure(pdf_page):
        return img_bgr.shape[1]   # W — full-width, no column restriction

    H, W  = img_bgr.shape[:2]
    zone  = img_bgr[search_y1:search_y2, :, :]
    gray  = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    sm    = uniform_filter1d(np.mean(gray < 220, axis=0).astype(float), size=20)

    gap = _find_column_gap(sm, W, cap_x + 50, cap_x + 700)
    if not gap:
        return W
    _, gend, _ = gap
    right_d = np.mean(sm[gend: min(W, gend + 300)])
    if right_d <= 0.04:
        return W
    return gend if _has_top_band_content(img_bgr, gend + 20, W) else W


def find_tight_top(img_bgr, search_top, cap_y, col_left=0, col_right=None,
                   min_gap=30):
    """
    Scan rows in x[col_left:col_right] between search_top and cap_y.
    Returns the y where the gap with the MOST content after it ends.
    This is the separator between body text above and figure below:
    the figure has more content rows than the caption text below it.

    Uses min_gap=30px to ignore small line-spacing gaps within text/figures.
    """
    H, W = img_bgr.shape[:2]
    if col_right is None:
        col_right = W
    roi = img_bgr[search_top: cap_y - 5, col_left:col_right]
    if roi.size == 0:
        return search_top
    gray    = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    row_has = np.any(gray < 220, axis=1)
    n       = len(row_has)

    gaps = []
    gs, gl = None, 0
    for y, has in enumerate(row_has):
        if not has:
            if gs is None:
                gs = y
            gl += 1
        else:
            if gl >= min_gap:
                gaps.append((gs, y, gl))
            gs, gl = None, 0
    if gl >= min_gap and gs is not None:
        gaps.append((gs, n, gl))

    if not gaps:
        return search_top

    # Pick the gap that has the most content after it.
    # That is the separator between body text (above) and figure (below):
    # the figure occupies more rows than any trailing caption text.
    best_gap = max(gaps, key=lambda g: sum(row_has[g[1]:]))
    return search_top + best_gap[1]


def get_image_rect_tight_top(page, cap_y_frac, zoom=200/72.0):
    """
    Get the topmost embedded image rect on this PDF page that falls above
    the caption (cap_y_frac as fraction of page height).
    Returns the y pixel coordinate (at ZOOM scale) of that top, or None.
    Uses fitz image rects which are authoritative — avoids the gap-scan
    being confused by vector drawings from adjacent figures.
    """
    pw, ph = page.rect.width, page.rect.height
    all_rects = []
    for img_item in page.get_images(full=True):
        try:
            for r in page.get_image_rects(img_item[0]):
                # Only count images in the figure zone (above caption, below header)
                if r.y1 / ph < cap_y_frac and r.y0 / ph > 0.10:
                    all_rects.append(r)
        except:
            pass
    if not all_rects:
        return None
    min_y_pts = min(r.y0 for r in all_rects)
    return int(min_y_pts * zoom)   # convert to pixel coords


def find_figure_zone_top(img_bgr, search_top, cap_y, min_gap=20):
    """
    Find the start of the LAST content block between search_top and cap_y.
    This is the true figure top, even when body text appears above it
    with no column separator.
    Uses full-width row scanning to find content blocks separated by gaps.
    """
    H, W     = img_bgr.shape[:2]
    search_bot = max(search_top + 20, cap_y - 10)
    roi      = img_bgr[search_top:search_bot, 0:W]
    gray     = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    row_has_content = np.any(gray < 220, axis=1)

    blocks = []
    in_block = False
    block_start = 0
    consec_blank = 0

    for y, has in enumerate(row_has_content):
        if has:
            if not in_block:
                block_start = y
                in_block = True
            consec_blank = 0
        else:
            if in_block:
                consec_blank += 1
                if consec_blank >= min_gap:
                    blocks.append(search_top + block_start)
                    in_block = False
                    consec_blank = 0

    if in_block:
        blocks.append(search_top + block_start)

    return blocks[-1] if blocks else search_top


def extract_figure_region(img_bgr, cap_y, cap_x, is_right_col,
                           search_top_override=None, pdf_page=None):
    """
    Find the bounding box of figure content above cap_y, restricted to
    the figure's own column.  Returns (x1, y1, x2, y2) or None.
    """
    H, W = img_bgr.shape[:2]

    search_top = (search_top_override if search_top_override is not None
                  else max(0, int(H * PAGE_HEADER)))
    search_bot = max(search_top + 20, cap_y - 10)
    if search_bot <= search_top:
        return None

    if is_right_col:
        col_left  = find_right_col_left_boundary(img_bgr, cap_x, search_top, search_bot)
        col_right = W
    else:
        col_left  = 0
        col_right = find_left_col_right_boundary(img_bgr, cap_x, search_top, search_bot,
                                                  pdf_page=pdf_page)

    def run_contours(x1, x2):
        if x2 <= x1:   # guard against empty column slice
            return None
        # Use image-rect tight_top when reliable, cross-checked with gap scan.
        img_rect_top = get_image_rect_tight_top(pdf_page, cap_y / H) \
                       if pdf_page is not None else None
        gap_top = find_tight_top(img_bgr, search_top, cap_y, x1, x2)
        if img_rect_top is not None and img_rect_top > search_top:
            rect_top = max(search_top, img_rect_top - 15)  # small pad above images
            # If image rect top is much lower than gap top, it likely points to a
            # lower sub-panel only (e.g. Figure 7.6(c)). Prefer gap-based top then.
            if rect_top - gap_top > 180:
                tight = gap_top
            else:
                tight = min(rect_top, gap_top)
        else:
            tight = gap_top
        if search_bot <= tight:
            return None
        roi  = img_bgr[tight:search_bot, x1:x2]
        if roi.size == 0:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY_INV)
        # Remove OCR-detected text blocks to avoid paragraph/caption bleed.
        text_mask = _text_mask_from_ocr(roi, conf_thresh=35)
        thresh_masked = thresh.copy()
        thresh_masked[text_mask > 0] = 0
        pa   = H * W
        def kept_from_binary(bin_img):
            dil = cv2.dilate(
                bin_img,
                np.ones((DILATE_K, DILATE_K), np.uint8),
                iterations=DILATE_ITER,
            )
            cnts, _ = cv2.findContours(dil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            out = []
            for cnt in cnts:
                bx, by, bw, bh = cv2.boundingRect(cnt)
                abs_x1 = x1 + bx
                abs_x2 = abs_x1 + bw
                is_decoration = (abs_x1 / W < 0.02) and (abs_x2 / W > MAX_DECOR_W)
                is_tiny       = (bw * bh) / pa < MIN_FIG_AREA
                if not is_decoration and not is_tiny:
                    out.append(cnt)
            return out

        kept = kept_from_binary(thresh_masked)
        if not kept:
            # If masking removed everything (common for figures with many labels),
            # retry once without text masking.
            kept = kept_from_binary(thresh)
            if not kept:
                return None
        pts = np.concatenate(kept)
        fx, fy, fw, fh = cv2.boundingRect(pts)
        masked_bbox = (x1 + fx, tight + fy, x1 + fx + fw, tight + fy + fh)

        # If masking over-trims right/left figure labels, prefer raw bbox when
        # it is materially wider but vertically similar.
        raw_kept = kept_from_binary(thresh)
        if raw_kept:
            rpts = np.concatenate(raw_kept)
            rx, ry, rw, rh = cv2.boundingRect(rpts)
            raw_bbox = (x1 + rx, tight + ry, x1 + rx + rw, tight + ry + rh)
            mw = masked_bbox[2] - masked_bbox[0]
            rwid = raw_bbox[2] - raw_bbox[0]
            mh = masked_bbox[3] - masked_bbox[1]
            rhgt = raw_bbox[3] - raw_bbox[1]
            if rwid > 1.12 * max(1, mw) and abs(rhgt - mh) < 0.25 * max(1, mh):
                return raw_bbox

        return masked_bbox

    bbox = run_contours(col_left, col_right)
    if bbox:
        # If bbox hugs a detected column boundary, it is likely clipped.
        # Retry full-width and keep it if it expands area materially.
        bx1, by1, bx2, by2 = bbox
        touch_right = (not is_right_col) and (col_right < W) and (abs(bx2 - col_right) <= 24)
        # Restrict this fallback to left-column cases. On right-column pages it can
        # leak left-side body text into crops (e.g., Fig 7.17 page).
        if touch_right:
            wide = run_contours(0, W)
            if wide is not None:
                wx1, wy1, wx2, wy2 = wide
                b_area = max(1, (bx2 - bx1) * (by2 - by1))
                w_area = max(1, (wx2 - wx1) * (wy2 - wy1))
                if w_area > 1.12 * b_area:
                    return wide
        # Left-column multi-part figures can have faint left sub-panels that
        # contouring misses. If bbox starts unusually far right of caption start,
        # gently extend leftward toward caption anchor.
        if (not is_right_col) and (cap_x / W < 0.25):
            target_left = max(0, cap_x - 180)
            if bx1 > target_left + 60:
                bbox = (target_left, by1, bx2, by2)
        return bbox
    # Fallback to full width ONLY if column boundaries matched (no separator found)
    # i.e. col_right == W for left-col and col_left == 0 for right-col
    # This prevents leaking body text when a separator WAS found but no content in slice
    if (not is_right_col and col_right == W) or (is_right_col and col_left == 0):
        return run_contours(0, W)

    # Rescue fallback: sometimes caption x-position is left-of-center but the
    # actual figure block sits mostly in the middle/right (e.g. Fig 7.19).
    # If strict left-column slice finds nothing, try full-width and keep only
    # sufficiently large candidates that remain above caption.
    if not is_right_col and col_right < W:
        wide = run_contours(0, W)
        if wide is not None:
            wx1, wy1, wx2, wy2 = wide
            if (wx2 - wx1) > int(0.35 * W) and wy2 < cap_y - 5:
                return wide

    # Last-resort retry for stacked figures:
    # if advancing search_top became too aggressive and no region was found,
    # retry once from default header top.
    default_top = max(0, int(H * PAGE_HEADER))
    if search_top_override is not None and search_top > default_top + 20:
        return extract_figure_region(
            img_bgr,
            cap_y,
            cap_x,
            is_right_col,
            search_top_override=None,
            pdf_page=pdf_page,
        )
    return None


def save_figure(img_bgr, page_num, label, x1, y1, x2, y2, expected_fig_id=None):
    H, W = img_bgr.shape[:2]
    PAD  = 12
    crop = img_bgr[max(0,y1-PAD):min(H,y2+PAD), max(0,x1-PAD):min(W,x2+PAD)]
    if crop.size == 0 or crop.shape[0] < 40 or crop.shape[1] < 40:
        return None
    crop = cv2.copyMakeBorder(crop, 12, 12, 12, 12,
                               cv2.BORDER_CONSTANT, value=(255, 255, 255))
    # Guard against false positives where crop caption doesn't match target figure.
    detected_fig_id = _detect_caption_id_in_crop(crop)
    if expected_fig_id and detected_fig_id and detected_fig_id != expected_fig_id:
        # If expected id exists anywhere in crop caption text, allow it.
        all_ids = _all_caption_ids_in_crop(crop)
        if expected_fig_id not in all_ids:
            return None

    score, text_cov, non_text_ink = _crop_quality_metrics(crop)
    # Guard against text-dominant crops (handles inline-reference false positives).
    if text_cov > TEXT_HEAVY_TEXT_COV and non_text_ink < TEXT_HEAVY_NON_TEXT_INK:
        return None
    # Extra strict when expected id is absent from crop OCR.
    if expected_fig_id and detected_fig_id is None and non_text_ink < 0.012:
        return None

    fname = f"p{page_num:03d}_{label}.png"
    cv2.imwrite(os.path.join(OUT_DIR, fname), crop)
    return fname


def dedupe_extracted_figures(results, out_dir):
    """
    Same Figure_X_Y may appear on multiple pages; keep the highest-quality crop
    and delete the rest. results: list of (page, label, fname).
    Returns sorted list of kept (page, label, fname).
    """
    scored = []
    for page, label, fname in results:
        fpath = os.path.join(out_dir, fname)
        img = cv2.imread(fpath)
        if img is None:
            continue
        score, _, _ = _crop_quality_metrics(img)
        scored.append((score, page, label, fname))

    by_label = {}
    for score, page, label, fname in scored:
        if label not in by_label or score > by_label[label][0]:
            by_label[label] = (score, page, label, fname)

    winners = {t[3] for t in by_label.values()}
    for score, page, label, fname in scored:
        if fname not in winners:
            try:
                os.remove(os.path.join(out_dir, fname))
            except FileNotFoundError:
                pass

    return sorted({(t[1], t[2], t[3]) for t in by_label.values()})


def write_figure_index(results, out_dir):
    """Write index.txt for sorted (page, label, fname) rows."""
    idx_path = os.path.join(out_dir, "index.txt")
    with open(idx_path, "w") as f:
        f.write("NCERT Class 11 Biology — Extracted Figures\n")
        f.write("=" * 60 + "\n\n")
        for page, label, fname in sorted(results):
            f.write(f"Page {page:>3}  {label:<32}  {fname}\n")
    return idx_path


def dedupe_only_disk(out_dir=None):
    """
    Scan out_dir for pNNN_Figure_X_Y.png files, dedupe by label, rewrite index.
    Use after an interrupted extract run so duplicate labels are cleaned up.
    """
    root = out_dir or OUT_DIR
    results = []
    for fname in sorted(os.listdir(root)):
        m = _FIGURE_FNAME_RE.match(fname)
        if not m:
            continue
        results.append((int(m.group(1)), m.group(2), fname))
    results = dedupe_extracted_figures(results, root)
    idx_path = write_figure_index(results, root)
    print(f"Deduped {root}/ — {len(results)} unique figures, index: {idx_path}")
    return results


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    doc     = fitz.open(PDF_PATH)
    total   = len(doc)
    results = []

    print("NCERT Biology Figure Extractor v6")
    print(f"Processing {total} pages...\n")

    for pn in range(total):
        page = doc[pn]

        if not _page_has_figure_ref(page):
            if (pn + 1) % 50 == 0:
                print(f"  [{pn+1}/{total}] {len(results)} figures so far")
            continue

        img_bgr  = render(page)
        H, W     = img_bgr.shape[:2]
        captions = find_all_captions(img_bgr)

        if not captions:
            del img_bgr
            continue

        # Per-column search_top tracker.
        # After extracting each figure, we set last_y2[col] to the y where
        # the NEXT content block in that column begins (found via blank-gap scan).
        # This prevents a figure's search zone from absorbing a sibling figure
        # that was missed by the OCR caption finder (e.g. "Figure 4.1 (b)").
        last_y2 = {
            "left":  int(H * PAGE_HEADER),
            "right": int(H * PAGE_HEADER),
        }

        for cap_idx, cap in enumerate(captions):
            fig_id       = cap["fig_id"]
            label        = f"Figure_{fig_id.replace('.', '_')}"
            cap_x        = cap["cap_x"]
            cap_y        = cap["cap_y"]
            cap_y2       = cap["cap_y2"]
            is_right_col = cap["is_right_col"]
            col_key      = "right" if is_right_col else "left"

            bbox = extract_figure_region(
                img_bgr, cap_y, cap_x, is_right_col,
                search_top_override=last_y2[col_key],
                pdf_page=page,
            )
            if bbox is None:
                print(f"  ✗ p{pn+1:03d}  {label}  — no region found")
                continue

            fx1, fy1, fx2, fy2 = bbox

            # Find content below cap_y2 in the same column.
            # Some figures have multiple sub-parts where the first sub-caption
            # appears BETWEEN sub-figures, e.g.:
            #   [starfish]  ← sub-figure (a)
            #   "Figure 4.1 (a) Radial symmetry"  ← cap_y / cap_y2
            #   [crab]      ← sub-figure (b)  ← this would be missed otherwise
            #   "Figure 4.1 (b) Bilateral symmetry"
            # We scan downward from cap_y2 to find the next blank gap;
            # everything up to that gap belongs to this figure.
            next_cap_y = H
            next_cap_fig_id = None
            for future_cap in captions[cap_idx + 1:]:
                future_col = "right" if future_cap["is_right_col"] else "left"
                if future_col == col_key:
                    next_cap_y = future_cap["cap_y"]
                    next_cap_fig_id = future_cap["fig_id"]
                    break

            # Use cap_x for a tight column boundary (avoids adjacent body text)
            col_left_px  = max(0, cap_x - 50) if is_right_col else 0
            col_right_px = W if is_right_col else int(W * RIGHT_COL_THRESHOLD)

            content_bottom = content_extent_in_column(
                img_bgr,
                from_y    = cap_y2,
                to_y      = next_cap_y - 10,
                col_left  = col_left_px,
                col_right = col_right_px,
            )

            # crop_y2:
            # - Right-column figures: use content_bottom to capture any
            #   sub-figures that appear BELOW the first sub-caption
            #   (e.g. Fig 4.1: starfish → caption(a) → crab → caption(b))
            # - Left-column figures: use cap_y2 directly; their captions
            #   always appear at the bottom after all sub-panels, so there
            #   is no figure content below the caption.
            crop_y2 = max(cap_y2, content_bottom - 15) if is_right_col else cap_y2

            # Guardrail for stacked figures in the same column:
            # never allow current crop to extend into next caption block.
            if next_cap_y < H:
                crop_y2 = min(crop_y2, max(cap_y2, next_cap_y - 20))
                if is_right_col:
                    # When another same-column caption exists, keep extension tight
                    # so current figure does not absorb the next stacked figure.
                    crop_y2 = min(crop_y2, cap_y2 + MAX_RIGHT_EXTEND_WITH_NEXT_CAP)
                    # If the next caption belongs to a different figure, do not
                    # extend below current caption at all.
                    if next_cap_fig_id and next_cap_fig_id != fig_id:
                        crop_y2 = cap_y2

            # When crop_y2 extends beyond cap_y2 (i.e. sub-figures below caption),
            # also expand fx1/fx2 to cover the full x-extent of that sub-content.
            # Example: starfish is narrow (59-78%) but crab below caption is wide (10-86%).
            # Clamp expansion to col_left_px so left-column body text is excluded.
            if crop_y2 > cap_y2 + 20:
                sub_zone = img_bgr[cap_y2:content_bottom, :, :]
                gray_sub = cv2.cvtColor(sub_zone, cv2.COLOR_BGR2GRAY)
                _, thresh_sub = cv2.threshold(gray_sub, 235, 255, cv2.THRESH_BINARY_INV)
                cols_with_content = np.any(thresh_sub > 0, axis=0)
                sub_xs = [x for x, v in enumerate(cols_with_content) if v]
                if sub_xs:
                    # For right-col figures: don't expand left of col_left_px
                    # (keeps body text out of the crop)
                    sub_x1 = max(sub_xs[0], col_left_px) if is_right_col else sub_xs[0]
                    sub_x2 = sub_xs[-1]
                    fx1 = min(fx1, sub_x1)
                    fx2 = max(fx2, sub_x2)
            # last_y2 update:
            # - Right-col: use content_bottom so crab-type sub-figures below
            #   a sub-caption gate the next figure correctly.
            # - Left-col: use cap_y2 directly. Left-col stacked figures have
            #   NO blank gap between a caption and the next figure
            #   (e.g. Fig4.10 caption sits right above Fig4.11 content).
            #   content_bottom would return next_cap_y here, pushing search_top
            #   to 88% and leaving no room for Fig4.11's bbox search.
            # last_y2 update:
            # - Right-column: use content_bottom (handles stacked sub-figures like 4.1a/4.1b)
            # - Left-column: use cap_y2 (figures are contiguous top-to-bottom,
            #   no gap between stacked figures; using cap_y2 correctly gates the next figure)
            if is_right_col:
                next_guard = (next_cap_y - 20) if next_cap_y < H else content_bottom
                cap_guard = cap_y2 + MAX_RIGHT_EXTEND_BELOW_CAPTION if next_cap_y < H else content_bottom
                last_y2[col_key] = min(content_bottom, next_guard, cap_guard)
            else:
                last_y2[col_key] = cap_y2

            fname = save_figure(
                img_bgr,
                pn + 1,
                label,
                fx1,
                fy1,
                fx2,
                crop_y2,
                expected_fig_id=fig_id,
            )
            if fname:
                results.append((pn + 1, label, fname))
                tag = "R" if is_right_col else "L"
                print(
                    f"  ✓ p{pn+1:03d} [{tag}] {label:<30}"
                    f"  x[{fx1/W:.0%}–{fx2/W:.0%}]"
                    f"  y[{fy1/H:.0%}–{crop_y2/H:.0%}]"
                )

        del img_bgr

        if (pn + 1) % 50 == 0:
            print(f"\n  ── [{pn+1}/{total}] {len(results)} extracted ──\n")

    doc.close()

    results = dedupe_extracted_figures(results, OUT_DIR)
    idx_path = write_figure_index(results, OUT_DIR)

    print(f"\n{'='*60}")
    print(f"Total figures extracted : {len(results)}")
    print(f"Output folder           : {OUT_DIR}/")
    print(f"Index file              : {idx_path}")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NCERT figure extractor / dedupe")
    ap.add_argument(
        "--pdf",
        default=PDF_PATH,
        help="Path to input PDF (default: books/NCERT-Class-11-Biology.pdf)",
    )
    ap.add_argument(
        "--out",
        default=OUT_DIR,
        help="Output directory for extracted figures and index.txt",
    )
    ap.add_argument(
        "--dedupe-only",
        action="store_true",
        help="Only dedupe existing PNGs in OUT_DIR and rewrite index.txt (no PDF pass)",
    )
    args = ap.parse_args()

    # Allow running extractor on arbitrary PDF / output dir without editing code.
    PDF_PATH = os.path.abspath(args.pdf)
    OUT_DIR = os.path.abspath(args.out)
    os.makedirs(OUT_DIR, exist_ok=True)

    if args.dedupe_only:
        dedupe_only_disk(OUT_DIR)
    else:
        main()
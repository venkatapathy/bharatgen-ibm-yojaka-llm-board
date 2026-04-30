

import argparse
import csv
import os
import re
import subprocess
import sys
from pathlib import Path

# ── dependency check ────────────────────────────────────────────────────────
try:
    import pdfplumber
except ImportError:
    sys.exit("❌  pdfplumber not found.  Run:  pip install pdfplumber")

try:
    from PIL import Image
except ImportError:
    sys.exit("❌  Pillow not found.  Run:  pip install Pillow")

if subprocess.run(["which", "pdftoppm"], capture_output=True).returncode != 0:
    sys.exit("❌  pdftoppm not found.  Run:  sudo apt-get install poppler-utils")


# ── constants ────────────────────────────────────────────────────────────────
DEFAULT_PDF = "/home/pankaj/code/bharatgen-ibm-yojaka-llm-board/backend/mm_rag/books/BharatGen_Yojaka_Multilingual_NCERT_Books/Hindi/Biology/Class-11/Hindi_Biology_Class-11.pdf"
DEFAULT_DPI = 150          # 150 = fast+readable; 200-300 for print quality
DEFAULT_OUT = "./figures"

# Hindi transliteration for "figure": fp=k <chapter>-<num>[sub]
# Matches: "fp=k 2-3"  "fp=k 11-10"  "fp=k 3-1 v"  "fp=k 2-4c"
CAP_WORD   = "fp=k"

# When searching for the figure's top boundary, ignore text that is within
# this many PDF-points above the caption (it may be an inline label, not body)
LABEL_CLEARANCE_PT = 25

# Extra whitespace (pts) added below caption bottom when cropping
CAP_MARGIN_BELOW   = 6

# Extra whitespace (pts) added above figure start when cropping
FIG_MARGIN_ABOVE   = 2

# Minimum crop height in PDF points — skip crops smaller than this
MIN_CROP_HEIGHT_PT = 30


# ── helpers ──────────────────────────────────────────────────────────────────

def find_captions_on_page(page) -> list[dict]:
    """
    Scan a single pdfplumber Page and return a list of caption dicts,
    each with keys: label, num, sub, cap_top, cap_bottom, page_height, page_width.

    The caption word "fp=k" is immediately followed by a number token like
    "2-3" or "11-10", and optionally a single-letter sub-figure marker.
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    captions = []

    for i, w in enumerate(words):
        if w["text"] != CAP_WORD:
            continue

        if i + 1 >= len(words):
            continue
        num_w = words[i + 1]
        raw_num = num_w["text"].strip().replace(".", "-")

        # Validate: must look like  <digit>-<digit>  or  <digit>-<digits>
        if not re.match(r"^\d{1,2}-\d{1,2}$", raw_num):
            continue

        # Optional single-letter sub-figure (next word, e.g. "v", "c", "n")
        sub = ""
        if (i + 2 < len(words)
                and len(words[i + 2]["text"]) == 1
                and words[i + 2]["text"].isalpha()
                and words[i + 2]["top"] - w["top"] < 5):   # same line
            sub = words[i + 2]["text"].lower()

        label = raw_num + (f"_{sub}" if sub else "")

        cap_top    = min(w["top"],    num_w["top"])
        cap_bottom = max(w["bottom"], num_w["bottom"])

        captions.append({
            "label"      : label,
            "num"        : raw_num,
            "sub"        : sub,
            "cap_top"    : cap_top,
            "cap_bottom" : cap_bottom,
            "page_height": page.height,
            "page_width" : page.width,
        })

    return captions


def figure_top_pt(words: list[dict], cap_top: float) -> float:
    """
    Given all words on a page and the caption's top y-coordinate,
    return the y-coordinate (bottom of last word) that marks the
    figure's top boundary.

    Strategy: find the bottom of the last text word that sits more than
    LABEL_CLEARANCE_PT above the caption.  Everything between that point
    and the caption is the figure.
    """
    boundary = cap_top - LABEL_CLEARANCE_PT
    pre_cap_words = [w for w in words if w["bottom"] < boundary]
    if not pre_cap_words:
        return 0.0
    return max(w["bottom"] for w in pre_cap_words) + FIG_MARGIN_ABOVE


def rasterise_page(pdf_path: str, page_num: int, dpi: int,
                   tmp_dir: Path) -> Path:
    """Render a single page to JPEG with pdftoppm and return the file path."""
    # Clean up previous page files in tmp_dir
    for f in tmp_dir.glob("page-*.jpg"):
        f.unlink()

    prefix = str(tmp_dir / "page")
    r = subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi),
         "-f", str(page_num), "-l", str(page_num),
         pdf_path, prefix],
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"pdftoppm failed on page {page_num}: {r.stderr.decode()}")

    matches = sorted(tmp_dir.glob("page-*.jpg"))
    if not matches:
        raise FileNotFoundError(
            f"pdftoppm produced no output for page {page_num}")
    return matches[0]


def crop_and_save(img_path: Path,
                  fig_top_pt: float,
                  cap_bottom_pt: float,
                  page_h_pt: float,
                  dpi: int,
                  out_path: Path) -> tuple[int, int] | None:
    """
    Crop the figure region from a rasterised page image.

    All y-coordinates are in PDF points (origin = top of page).
    Returns the (width, height) of the saved crop, or None if too small.
    """
    scale = dpi / 72.0        # 72 PDF points per inch

    px_top    = max(0, int(fig_top_pt * scale))
    px_bottom = int((cap_bottom_pt + CAP_MARGIN_BELOW) * scale)

    if (px_bottom - px_top) < int(MIN_CROP_HEIGHT_PT * scale):
        return None

    img = Image.open(img_path)
    px_bottom = min(img.height, px_bottom)

    crop = img.crop((0, px_top, img.width, px_bottom))
    crop.save(out_path, "PNG")
    return crop.size


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract fp=k (चित्र) figures from Hindi Biology Class-11 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pdf",   nargs="?", default=DEFAULT_PDF,
                        help=f"Path to PDF  (default: {DEFAULT_PDF})")
    parser.add_argument("--dpi", type=int,  default=DEFAULT_DPI,
                        help=f"Rasterisation DPI (default {DEFAULT_DPI})")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"Output directory (default {DEFAULT_OUT})")
    parser.add_argument("--pages", type=int, nargs="+",
                        help="Process only these page numbers (1-based)")
    args = parser.parse_args()

    pdf_path = args.pdf
    out_dir  = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir  = Path("/tmp/_fig_extract")
    tmp_dir.mkdir(exist_ok=True)

    if not Path(pdf_path).exists():
        sys.exit(f"❌  PDF not found: {pdf_path}")

    # ── Step 1: discover page count ──────────────────────────────────────────
    r = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    m = re.search(r"Pages:\s+(\d+)", r.stdout)
    total_pages = int(m.group(1)) if m else 264
    print(f"📄  PDF: {pdf_path}  ({total_pages} pages)")

    # ── Step 2: scan every page for captions, build page→captions map ────────
    print(f"\n🔍  Scanning {total_pages} pages for figure captions …")

    # seen labels → skip cross-references; keep only FIRST occurrence
    seen_labels: set[str] = set()

    # page_num (1-based) → list of caption dicts (sorted by cap_top)
    page_map: dict[int, list[dict]] = {}

    page_range = args.pages if args.pages else range(1, total_pages + 1)

    for page_num in page_range:
        try:
            with pdfplumber.open(pdf_path, pages=[page_num]) as pdf:
                page = pdf.pages[0]
                caps = find_captions_on_page(page)

                if not caps:
                    continue

                words = page.extract_words(x_tolerance=3, y_tolerance=3)

                for cap in caps:
                    label = cap["label"]
                    if label in seen_labels:
                        continue   # cross-reference; skip
                    seen_labels.add(label)

                    # Compute figure top boundary
                    cap["fig_top"] = figure_top_pt(words, cap["cap_top"])

                    page_map.setdefault(page_num, []).append(cap)

        except Exception as exc:
            print(f"  ⚠  Page {page_num}: skipped — {exc}")
            continue

        if page_num % 20 == 0:
            n = sum(len(v) for v in page_map.values())
            print(f"  … page {page_num}/{total_pages}  ({n} figures so far)")

    total_found = sum(len(v) for v in page_map.values())
    print(f"  ✅  Found {total_found} unique figures on {len(page_map)} pages\n")

    # ── Step 3: rasterise + crop ──────────────────────────────────────────────
    print(f"🖼   Rasterising and cropping at {args.dpi} DPI …")

    index_rows = []
    done = 0

    for page_num in sorted(page_map.keys()):
        caps = sorted(page_map[page_num], key=lambda c: c["cap_top"])

        print(f"  Page {page_num:3d} ({len(caps)} fig) … ", end="", flush=True)

        try:
            img_path = rasterise_page(pdf_path, page_num, args.dpi, tmp_dir)
        except Exception as exc:
            print(f"SKIP (raster) — {exc}")
            continue

        for cap in caps:
            label     = cap["label"]
            safe_name = label.replace(" ", "_").replace(".", "-")
            out_file  = out_dir / f"fig_{safe_name}.png"

            size = crop_and_save(
                img_path      = img_path,
                fig_top_pt    = cap["fig_top"],
                cap_bottom_pt = cap["cap_bottom"],
                page_h_pt     = cap["page_height"],
                dpi           = args.dpi,
                out_path      = out_file,
            )

            if size is None:
                print(f"\n    ⚠  {label}: crop too small, skipped", end="")
            else:
                index_rows.append({
                    "label"    : f"fp=k {label}",
                    "page"     : page_num,
                    "fig_top_y": round(cap["fig_top"],  1),
                    "cap_top_y": round(cap["cap_top"],  1),
                    "width_px" : size[0],
                    "height_px": size[1],
                    "file"     : str(out_file),
                })
            done += 1

        print("done")

    # ── Step 4: write index CSV ───────────────────────────────────────────────
    csv_path = out_dir / "index.csv"
    fieldnames = ["label", "page", "fig_top_y", "cap_top_y",
                  "width_px", "height_px", "file"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(index_rows)

    print(f"\n✅  Extracted {len(index_rows)} figures  →  {out_dir}/")
    print(f"   Index  →  {csv_path}")
    skipped = done - len(index_rows)
    if skipped:
        print(f"   ⚠  {skipped} figure(s) skipped (crop too small)")


if __name__ == "__main__":
    main()
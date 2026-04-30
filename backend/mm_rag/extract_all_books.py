"""
Batch extractor for all PDFs under BharatGen_Yojaka_Multilingual_NCERT_Books.

It runs extract_ncert.py for each PDF and writes figures to per-book output
directories under backend/mm_rag/outputs/all_books/.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


BASE = Path(__file__).resolve().parent
DEFAULT_BOOKS_ROOT = BASE / "books" / "BharatGen_Yojaka_Multilingual_NCERT_Books"
DEFAULT_OUTPUT_ROOT = BASE / "outputs" / "all_books"
DEFAULT_EXTRACTOR = BASE / "extract_ncert.py"
DEFAULT_HINDI_EXTRACTOR = BASE / "extract_ncert_hindi.py"


def slugify_relpath(pdf: Path, books_root: Path) -> str:
    rel = pdf.relative_to(books_root)
    # Keep meaningful structure in folder names.
    s = str(rel.with_suffix(""))
    s = s.replace("\\", "_").replace("/", "_")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return s.lower()


def discover_pdfs(books_root: Path) -> list[Path]:
    return sorted(p for p in books_root.rglob("*.pdf") if p.is_file())


def is_already_extracted(out_dir: Path) -> bool:
    """
    Consider a book done if index.txt exists, has at least one 'Page' entry,
    and at least one extracted figure png exists.
    """
    idx = out_dir / "index.txt"
    if not idx.is_file():
        return False
    try:
        lines = idx.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return False
    has_page_entries = any(ln.lstrip().startswith("Page") for ln in lines)
    has_pngs = any(out_dir.glob("p*_Figure_*.png"))
    return has_page_entries and has_pngs


def choose_extractor_for_pdf(pdf: Path, default_extractor: Path, hindi_extractor: Path) -> Path:
    """
    Route Hindi books to Hindi-specific extractor by path segment match.
    """
    parts_lower = {p.lower() for p in pdf.parts}
    if "hindi" in parts_lower and hindi_extractor.is_file():
        return hindi_extractor
    return default_extractor


def run_one(extractor: Path, pdf: Path, out_dir: Path, python_bin: str, timeout_s: int) -> tuple[bool, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        python_bin,
        str(extractor),
        "--pdf",
        str(pdf),
        "--out",
        str(out_dir),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout_s}s"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        return False, msg[:2000]
    return True, (proc.stdout or "").strip()[-1500:]


def main() -> int:
    ap = argparse.ArgumentParser(description="Run extract_ncert.py for all books PDFs")
    ap.add_argument("--books-root", default=str(DEFAULT_BOOKS_ROOT), help="Root folder containing all books PDFs")
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root output folder for per-book extractions")
    ap.add_argument("--extractor", default=str(DEFAULT_EXTRACTOR), help="Extractor script path (default: extract_ncert.py)")
    ap.add_argument(
        "--hindi-extractor",
        default=str(DEFAULT_HINDI_EXTRACTOR),
        help="Hindi extractor script path (used when PDF path includes /Hindi/)",
    )
    ap.add_argument("--python", default=sys.executable, help="Python executable to invoke extractor")
    ap.add_argument("--limit", type=int, default=0, help="Run only first N PDFs (0 = all)")
    ap.add_argument("--timeout", type=int, default=3600, help="Timeout seconds per PDF extraction")
    ap.add_argument("--force", action="store_true", help="Re-run even if output already exists")
    ap.add_argument("--dry-run", action="store_true", help="Show planned runs only")
    args = ap.parse_args()

    books_root = Path(args.books_root).resolve()
    output_root = Path(args.output_root).resolve()
    extractor = Path(args.extractor).resolve()
    hindi_extractor = Path(args.hindi_extractor).resolve()

    if not books_root.is_dir():
        print(f"ERROR: books root not found: {books_root}", file=sys.stderr)
        return 1
    if not extractor.is_file():
        print(f"ERROR: extractor script not found: {extractor}", file=sys.stderr)
        return 1
    if not hindi_extractor.is_file():
        print(f"WARNING: Hindi extractor not found: {hindi_extractor}", file=sys.stderr)
        print("         Hindi PDFs will fall back to default extractor.", file=sys.stderr)

    pdfs = discover_pdfs(books_root)
    if args.limit > 0:
        pdfs = pdfs[: args.limit]

    print(f"Books root : {books_root}")
    print(f"Extractor  : {extractor}")
    print(f"Hindi ext. : {hindi_extractor}")
    print(f"Output root: {output_root}")
    print(f"PDF count  : {len(pdfs)}")
    if not pdfs:
        return 0

    plans = []
    for pdf in pdfs:
        name = slugify_relpath(pdf, books_root)
        out_dir = output_root / name
        plans.append((pdf, out_dir))

    if args.dry_run:
        for i, (pdf, out_dir) in enumerate(plans, 1):
            print(f"[{i:02d}] {pdf} -> {out_dir}")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    summary = {
        "books_root": str(books_root),
        "output_root": str(output_root),
        "extractor": str(extractor),
        "hindi_extractor": str(hindi_extractor),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(plans),
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "results": [],
    }

    for i, (pdf, out_dir) in enumerate(plans, 1):
        selected_extractor = choose_extractor_for_pdf(
            pdf=pdf,
            default_extractor=extractor,
            hindi_extractor=hindi_extractor,
        )

        if (not args.force) and is_already_extracted(out_dir):
            summary["skipped"] += 1
            print(f"\n[{i}/{len(plans)}] Skipping (already extracted): {pdf.name}")
            summary["results"].append(
                {
                    "pdf": str(pdf),
                    "out_dir": str(out_dir),
                    "extractor_used": str(selected_extractor),
                    "ok": True,
                    "skipped": True,
                    "elapsed_s": 0.0,
                    "log_tail": "Skipped: existing index.txt + figure files found.",
                }
            )
            continue

        print(f"\n[{i}/{len(plans)}] Extracting: {pdf.name}")
        print(f"  using: {selected_extractor.name}")
        t0 = time.time()
        ok, tail = run_one(
            extractor=selected_extractor,
            pdf=pdf,
            out_dir=out_dir,
            python_bin=args.python,
            timeout_s=args.timeout,
        )
        dt = round(time.time() - t0, 2)
        if ok:
            summary["ok"] += 1
            print(f"  ✓ done in {dt}s -> {out_dir}")
        else:
            summary["failed"] += 1
            print(f"  ✗ failed in {dt}s")
            print(f"    {tail[:400]}")
        summary["results"].append(
            {
                "pdf": str(pdf),
                "out_dir": str(out_dir),
                "extractor_used": str(selected_extractor),
                "ok": ok,
                "skipped": False,
                "elapsed_s": dt,
                "log_tail": tail,
            }
        )

    summary["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = output_root / "batch_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + "=" * 60)
    print(
        f"Completed. OK={summary['ok']}  SKIPPED={summary['skipped']}  FAILED={summary['failed']}"
    )
    print(f"Summary: {summary_path}")
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())


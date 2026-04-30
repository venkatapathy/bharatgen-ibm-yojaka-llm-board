"""
Batch runner: build figures.json metadata for all extracted English books.

It reads per-book extraction outputs from outputs/all_books/english_*/ and runs
structure_fig.py with per-book paths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_BOOKS_ROOT = BASE / "books" / "BharatGen_Yojaka_Multilingual_NCERT_Books" / "English"
DEFAULT_EXTRACTED_ROOT = BASE / "outputs" / "all_books"
DEFAULT_STRUCTURED_ROOT = BASE / "outputs" / "all_books_structured"
DEFAULT_STRUCTURER = BASE / "structure_fig.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run structure_fig.py for all English extracted books")
    ap.add_argument("--books-root", default=str(DEFAULT_BOOKS_ROOT), help="Root English books folder")
    ap.add_argument("--extracted-root", default=str(DEFAULT_EXTRACTED_ROOT), help="Root containing per-book extraction outputs")
    ap.add_argument("--structured-root", default=str(DEFAULT_STRUCTURED_ROOT), help="Root to write per-book figures.json files")
    ap.add_argument("--structurer", default=str(DEFAULT_STRUCTURER), help="Path to structure_fig.py")
    ap.add_argument("--python", default=sys.executable, help="Python interpreter")
    ap.add_argument("--limit", type=int, default=0, help="Run first N books only (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="Print planned jobs only")
    args = ap.parse_args()

    books_root = Path(args.books_root).resolve()
    extracted_root = Path(args.extracted_root).resolve()
    structured_root = Path(args.structured_root).resolve()
    structurer = Path(args.structurer).resolve()

    if not structurer.is_file():
        print(f"ERROR: structurer not found: {structurer}", file=sys.stderr)
        return 1

    english_dirs = sorted([p for p in extracted_root.glob("english_*") if p.is_dir()])
    if args.limit > 0:
        english_dirs = english_dirs[: args.limit]

    plans = []
    for out_dir in english_dirs:
        idx = out_dir / "index.txt"
        if not idx.is_file():
            continue

        # Recover relative book path from slug: english_subject_class-x_file
        # We map by fuzzy match against available English PDFs.
        slug = out_dir.name
        pdf_candidates = list(books_root.rglob("*.pdf"))
        slug_low = slug.lower().replace("-", "_")
        chosen = None
        for pdf in pdf_candidates:
            k = "_".join(pdf.relative_to(books_root).with_suffix("").parts).lower().replace("-", "_")
            if slug_low in k or k in slug_low:
                chosen = pdf
                break
        if chosen is None:
            # best-effort: pick first with same subject token
            toks = slug_low.split("_")
            chosen = next((p for p in pdf_candidates if any(t in p.name.lower() for t in toks[:4])), None)
        if chosen is None:
            continue

        out_json = structured_root / f"{slug}.figures.json"
        plans.append((chosen, out_dir, idx, out_json))

    print(f"Structurer    : {structurer}")
    print(f"Extracted root: {extracted_root}")
    print(f"Books root    : {books_root}")
    print(f"Plan count    : {len(plans)}")
    if args.dry_run:
        for i, (pdf, out_dir, idx, out_json) in enumerate(plans, 1):
            print(f"[{i:02d}] pdf={pdf.name}  idx={idx}  out={out_json}")
        return 0

    structured_root.mkdir(parents=True, exist_ok=True)
    summary = {"total": len(plans), "ok": 0, "failed": 0, "results": []}
    for i, (pdf, out_dir, idx, out_json) in enumerate(plans, 1):
        cmd = [
            args.python,
            str(structurer),
            "--pdf",
            str(pdf),
            "--index-file",
            str(idx),
            "--images-dir",
            str(out_dir),
            "--out-file",
            str(out_json),
        ]
        print(f"\n[{i}/{len(plans)}] {pdf.name}")
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        dt = round(time.time() - t0, 2)
        ok = proc.returncode == 0
        if ok:
            summary["ok"] += 1
            print(f"  ✓ {out_json} ({dt}s)")
        else:
            summary["failed"] += 1
            print(f"  ✗ failed ({dt}s)")
            print((proc.stderr or proc.stdout)[-400:])
        summary["results"].append(
            {
                "pdf": str(pdf),
                "index": str(idx),
                "images_dir": str(out_dir),
                "out_json": str(out_json),
                "ok": ok,
                "elapsed_s": dt,
            }
        )

    summary_path = structured_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nDone. OK={summary['ok']} FAILED={summary['failed']}")
    print(f"Summary: {summary_path}")
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())


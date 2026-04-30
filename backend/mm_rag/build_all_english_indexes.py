"""
Build multimodal indexes for all English books.

Pairs each structured figures file from outputs/all_books_structured/*.figures.json
with its source PDF under books/.../English, then calls build_index(...) once per book.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from build_multimodal_index import build_index
from mm_siglip import BASE_DIR

DEFAULT_BOOKS_ROOT = (
    Path(BASE_DIR) / "books" / "BharatGen_Yojaka_Multilingual_NCERT_Books" / "English"
)
DEFAULT_STRUCTURED_ROOT = Path(BASE_DIR) / "outputs" / "all_books_structured"
DEFAULT_INDEX_ROOT = Path(BASE_DIR) / "indexes" / "all_books_english"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _pdf_key(pdf_path: Path, books_root: Path) -> str:
    rel = pdf_path.relative_to(books_root).with_suffix("")
    return _norm("_".join(rel.parts))


def _slug_key(fig_json_path: Path) -> str:
    # english_biology_class-11_english_biology_class-11.figures.json
    stem = fig_json_path.name.replace(".figures.json", "")
    return _norm(stem)


def _pick_pdf_for_slug(slug: str, pdf_by_key: dict[str, Path]) -> Path | None:
    if slug in pdf_by_key:
        return pdf_by_key[slug]

    for k, p in pdf_by_key.items():
        if slug in k or k in slug:
            return p

    toks = [t for t in slug.split("_") if t]
    best_pdf = None
    best_score = 0
    for k, p in pdf_by_key.items():
        score = sum(1 for t in toks if t in k)
        if score > best_score:
            best_score = score
            best_pdf = p
    return best_pdf if best_score >= 3 else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Build all English multimodal indexes")
    ap.add_argument("--books-root", default=str(DEFAULT_BOOKS_ROOT), help="English books root")
    ap.add_argument(
        "--structured-root",
        default=str(DEFAULT_STRUCTURED_ROOT),
        help="Folder with *.figures.json files",
    )
    ap.add_argument("--index-root", default=str(DEFAULT_INDEX_ROOT), help="Output indexes root")
    ap.add_argument("--limit", type=int, default=0, help="Build first N only (0=all)")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if both index and meta already exist",
    )
    ap.add_argument("--dry-run", action="store_true", help="Only print planned mappings")
    args = ap.parse_args()

    books_root = Path(args.books_root).resolve()
    structured_root = Path(args.structured_root).resolve()
    index_root = Path(args.index_root).resolve()

    if not books_root.is_dir():
        raise SystemExit(f"Books root not found: {books_root}")
    if not structured_root.is_dir():
        raise SystemExit(f"Structured root not found: {structured_root}")

    pdfs = sorted(books_root.rglob("*.pdf"))
    pdf_by_key: dict[str, Path] = {_pdf_key(p, books_root): p for p in pdfs}

    fig_jsons = sorted(structured_root.glob("english_*.figures.json"))
    if args.limit > 0:
        fig_jsons = fig_jsons[: args.limit]

    plans: list[tuple[Path, Path, Path, Path]] = []
    for fig_json in fig_jsons:
        slug = _slug_key(fig_json)
        pdf = _pick_pdf_for_slug(slug, pdf_by_key)
        if pdf is None:
            print(f"⚠️ No PDF match for: {fig_json.name}")
            continue

        index_out = index_root / f"{slug}.faiss"
        meta_out = index_root / f"{slug}.meta.pkl"
        if (not args.force) and index_out.is_file() and meta_out.is_file():
            continue
        plans.append((pdf, fig_json, index_out, meta_out))

    print(f"Books root    : {books_root}")
    print(f"Structured    : {structured_root}")
    print(f"Index root    : {index_root}")
    print(f"Matched plans : {len(plans)}")

    if args.dry_run:
        for i, (pdf, fig_json, index_out, meta_out) in enumerate(plans, 1):
            print(f"[{i:02d}] pdf={pdf.name}")
            print(f"     fig={fig_json.name}")
            print(f"     out={index_out.name}, {meta_out.name}")
        return 0

    index_root.mkdir(parents=True, exist_ok=True)

    summary = {"total": len(plans), "ok": 0, "failed": 0, "results": []}
    for i, (pdf, fig_json, index_out, meta_out) in enumerate(plans, 1):
        print(f"\n[{i}/{len(plans)}] {pdf.name}")
        try:
            build_index(
                pdf_path=str(pdf),
                figures_json=str(fig_json),
                index_out=str(index_out),
                meta_out=str(meta_out),
            )
            summary["ok"] += 1
            ok = True
            err = ""
        except Exception as e:
            summary["failed"] += 1
            ok = False
            err = str(e)
            print(f"✗ failed: {e}")

        summary["results"].append(
            {
                "pdf": str(pdf),
                "figures_json": str(fig_json),
                "index_out": str(index_out),
                "meta_out": str(meta_out),
                "ok": ok,
                "error": err,
            }
        )

    summary_path = index_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nDone. OK={summary['ok']} FAILED={summary['failed']}")
    print(f"Summary: {summary_path}")
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())


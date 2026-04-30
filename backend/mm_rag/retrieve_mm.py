"""
Simple multimodal retriever: query text → top-k from FAISS (text chunks + figures).

Uses the same SigLIP text encoder and index as build_multimodal_index.py.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from mm_siglip import META_PATH, INDEX_PATH, INDEX_ROOT, SIGLIP_MODEL_ID, embed_text


def _row_from_rec(score: float, rec: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"score": float(score), "type": rec["type"]}
    if rec["type"] == "text":
        row["content"] = rec["content"]
    else:
        row["image_path"] = rec["image_path"]
        row["caption"] = rec.get("caption", "")
        row["page"] = rec.get("page")
        row["chapter"] = rec.get("chapter", "")
    return row


def load_index(
    index_path: str | None = None,
    meta_path: str | None = None,
):
    index_path = index_path or INDEX_PATH
    meta_path = meta_path or META_PATH
    if not os.path.isfile(index_path):
        raise FileNotFoundError(f"FAISS index not found: {index_path}")
    if not os.path.isfile(meta_path):
        raise FileNotFoundError(f"Metadata not found: {meta_path}")

    index = faiss.read_index(index_path)
    with open(meta_path, "rb") as f:
        records = pickle.load(f)
    return index, records


def _all_books_index_root() -> Path:
    return Path(INDEX_ROOT)


def _available_index_pairs(index_root: str | None = None) -> list[tuple[str, str]]:
    root = Path(index_root).resolve() if index_root else _all_books_index_root()
    if not root.is_dir():
        return []
    pairs: list[tuple[str, str]] = []
    for faiss_path in sorted(root.glob("*.faiss")):
        meta_path = faiss_path.with_name(f"{faiss_path.stem}.meta.pkl")
        if meta_path.is_file():
            pairs.append((str(faiss_path), str(meta_path)))
    return pairs


def _resolve_filtered_pairs(
    *,
    subject: str | None,
    class_level: str | None,
    part: str | None,
    language: str = "english",
    index_root: str | None = None,
) -> list[tuple[str, str]]:
    pairs = _available_index_pairs(index_root=index_root)
    if not pairs:
        return []

    subject_t = (subject or "").strip().lower().replace("-", "_")
    class_t = (class_level or "").strip().lower().replace("class-", "").replace("class_", "")
    lang_t = (language or "english").strip().lower()
    part_t = (part or "").strip().lower().replace("-", "_")
    if part_t in ("", "none", "na"):
        part_t = "all"

    out: list[tuple[str, str]] = []
    for idx_path, meta_path in pairs:
        stem = Path(idx_path).stem.lower()
        if lang_t and not stem.startswith(f"{lang_t}_"):
            continue
        if subject_t and f"_{subject_t}_" not in f"_{stem}_":
            continue
        if class_t and f"_class_{class_t}_" not in f"_{stem}_":
            continue
        if part_t == "1" and "_part_1" not in stem:
            continue
        if part_t == "2" and "_part_2" not in stem:
            continue
        if part_t in ("all", "both"):
            # keep all matching books regardless of part suffix
            pass
        out.append((idx_path, meta_path))
    return out


def _split_top_k(top_k: int, favor_figures: bool) -> tuple[int, int]:
    """How many text vs figure slots (must sum to top_k)."""
    if favor_figures:
        # Prefer more figure slots so images appear (e.g. k=5 → 2 text + 3 figures).
        figure_k = max(1, (top_k + 1) // 2)
        text_k = top_k - figure_k
    else:
        text_k = max(1, (top_k + 1) // 2)
        figure_k = top_k - text_k
    return text_k, figure_k


def retrieve(
    query: str,
    top_k: int = 5,
    index_path: str | None = None,
    meta_path: str | None = None,
    *,
    balanced: bool = True,
    favor_figures: bool = True,
    num_text: int | None = None,
    num_figures: int | None = None,
    subject: str | None = None,
    class_level: str | None = None,
    part: str | None = None,
    language: str = "english",
    index_root: str | None = None,
) -> list[dict[str, Any]]:
    """
    Encode `query` with SigLIP text tower and search the multimodal index.

    Returns a list of dicts, each with:
      - score: inner product (cosine, since vectors are L2-normalized)
      - type: \"text\" | \"figure\"
      - For text: content
      - For figure: image_path, caption, page, chapter

    If ``balanced`` is True (default), searches the **full** index and fills
    separate quotas of best-matching **text** and **figure** rows. That avoids
    the common case where global top-k is all text because there are many more
    text chunks than figures.

    If ``num_text`` and/or ``num_figures`` are set, balanced mode uses those
    exact counts (zeros allowed for one side). When both are omitted, quotas
    follow ``top_k`` and ``favor_figures`` via ``_split_top_k``.

    If ``balanced`` is False, returns the single global top-``top_k`` (old behavior).
    """
    # If explicit index/meta is given, use only that pair.
    if index_path or meta_path:
        pairs = [(index_path or INDEX_PATH, meta_path or META_PATH)]
    else:
        pairs = _resolve_filtered_pairs(
            subject=subject,
            class_level=class_level,
            part=part,
            language=language,
            index_root=index_root,
        )
        if not pairs:
            if subject or class_level:
                raise FileNotFoundError(
                    f"No index found for language={language}, subject={subject}, class={class_level}, part={part or 'all'}"
                )
            # Fallback to default single index path for backward compatibility.
            pairs = [(INDEX_PATH, META_PATH)]

    q = embed_text(query).reshape(1, -1).astype(np.float32)
    faiss.normalize_L2(q)

    all_rows: list[dict[str, Any]] = []
    for idx_p, meta_p in pairs:
        index, records = load_index(idx_p, meta_p)
        if len(records) != index.ntotal:
            raise ValueError(
                f"Metadata length {len(records)} != index.ntotal {index.ntotal} for {idx_p}"
            )

        ntotal = index.ntotal
        scores, indices = index.search(q, ntotal)
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            row = _row_from_rec(float(score), records[int(idx)])
            row["source_index"] = idx_p
            all_rows.append(row)

    all_rows.sort(key=lambda r: -r["score"])

    if not balanced:
        return all_rows[: max(0, int(top_k))]

    if num_text is not None or num_figures is not None:
        text_k = 0 if num_text is None else max(0, int(num_text))
        figure_k = 0 if num_figures is None else max(0, int(num_figures))
        if text_k == 0 and figure_k == 0:
            text_k, figure_k = _split_top_k(top_k, favor_figures=favor_figures)
    else:
        text_k, figure_k = _split_top_k(top_k, favor_figures=favor_figures)
    text_out: list[dict[str, Any]] = []
    fig_out: list[dict[str, Any]] = []

    for rec in all_rows:
        if rec["type"] == "text" and len(text_out) < text_k:
            text_out.append(rec)
        elif rec["type"] == "figure" and len(fig_out) < figure_k:
            fig_out.append(rec)
        if len(text_out) >= text_k and len(fig_out) >= figure_k:
            break

    merged = fig_out + text_out
    merged.sort(key=lambda r: -r["score"])
    return merged


def main():
    ap = argparse.ArgumentParser(description="Query the multimodal NCERT index")
    ap.add_argument("query", nargs="?", help="Search query (or use --interactive)")
    ap.add_argument("-k", "--top-k", type=int, default=5, help="Number of results")
    ap.add_argument(
        "--global-only",
        action="store_true",
        help="Single global top-k (often all text); default is balanced text+figure",
    )
    ap.add_argument(
        "--more-text",
        action="store_true",
        help="With balanced mode, assign more slots to text than figures (default: more figures)",
    )
    ap.add_argument("-i", "--interactive", action="store_true", help="Read queries from stdin")
    ap.add_argument("--json", action="store_true", help="Print JSON only")
    args = ap.parse_args()

    if not args.query and not args.interactive:
        ap.print_help()
        print("\nExample: python retrieve_mm.py \"structure of cell membrane\" -k 3")
        raise SystemExit(1)

    print(f"SigLIP model: {SIGLIP_MODEL_ID}", flush=True)

    def run_one(q: str):
        hits = retrieve(
            q,
            top_k=args.top_k,
            balanced=not args.global_only,
            favor_figures=not args.more_text,
        )
        if args.json:
            print(json.dumps(hits, indent=2, ensure_ascii=False))
            return
        print(f"\nQuery: {q!r}\n")
        for i, h in enumerate(hits, 1):
            print(f"--- #{i}  score={h['score']:.4f}  type={h['type']} ---")
            if h["type"] == "text":
                preview = h["content"][:400] + ("…" if len(h["content"]) > 400 else "")
                print(preview)
            else:
                print(f"image: {h['image_path']}")
                print(f"page: {h.get('page')}  chapter: {h.get('chapter')}")
                cap = h.get("caption") or ""
                print(f"caption: {cap[:500]}{'…' if len(cap) > 500 else ''}")
            print()

    if args.interactive:
        for line in __import__("sys").stdin:
            q = line.strip()
            if not q:
                continue
            run_one(q)
    else:
        run_one(args.query)


if __name__ == "__main__":
    main()

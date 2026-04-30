import os
import json
import argparse
import faiss
import pickle
import numpy as np
from PIL import Image
from tqdm import tqdm

from pdf_text_chunks import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_WORDS, extract_text_chunks
from mm_siglip import (
    BASE_DIR,
    SIGLIP_MODEL_ID,
    INDEX_PATH,
    META_PATH,
    device,
    embed_text,
    embed_image,
    fix_image_path,
)

FIGURES_JSON = os.path.join(BASE_DIR, "outputs/figures.json")
PDF_PATH = os.path.join(BASE_DIR, "books/NCERT-Class-11-Biology.pdf")


def build_index(
    pdf_path: str = PDF_PATH,
    figures_json: str = FIGURES_JSON,
    index_out: str = INDEX_PATH,
    meta_out: str = META_PATH,
):

    records = []
    vectors = []

    # -----------------------------
    # TEXT
    # -----------------------------
    print("📖 Processing text...")
    print(f"🔧 SigLIP model: {SIGLIP_MODEL_ID} (device={device})")
    print(
        f"   Chunks: {DEFAULT_CHUNK_WORDS} words, overlap {DEFAULT_CHUNK_OVERLAP} (env: MM_RAG_CHUNK_WORDS, MM_RAG_CHUNK_OVERLAP)",
        flush=True,
    )

    chunks = extract_text_chunks(pdf_path)
    if not chunks:
        raise RuntimeError(
            "No readable text extracted from PDF. "
            "Ensure the input PDF has a readable text layer."
        )

    for chunk in tqdm(chunks, desc="Text embeddings", unit="chunk", dynamic_ncols=True):
        emb = embed_text(chunk)

        records.append({
            "type": "text",
            "content": chunk
        })

        vectors.append(emb)

    # -----------------------------
    # FIGURES
    # -----------------------------
    print("🖼️ Processing figures...")

    with open(figures_json) as f:
        figures = json.load(f)

    for fig in tqdm(figures, desc="Figure embeddings", unit="fig", dynamic_ncols=True):

        try:
            image_path = fix_image_path(fig["image_path"])
            caption = fig["caption"]

            pil = Image.open(image_path).convert("RGB")
            img_emb = embed_image(pil)
            cap_emb = embed_text(caption or "")
            emb = (img_emb + cap_emb) / 2.0

            records.append({
                "type": "figure",
                "image_path": image_path,
                "caption": caption,
                "page": fig["page"],
                "chapter": fig["chapter"]
            })

            vectors.append(emb)

        except Exception as e:
            print("⚠️ Skipping:", e)

    # -----------------------------
    # FAISS
    # -----------------------------
    print("📊 Building FAISS...", flush=True)

    vectors = np.array(vectors).astype("float32")

    # normalize for cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    vectors = vectors / norms

    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    # -----------------------------
    # SAVE
    # -----------------------------
    print("💾 Writing index and metadata...", flush=True)
    index_dir = os.path.dirname(index_out)
    meta_dir = os.path.dirname(meta_out)
    if index_dir:
        os.makedirs(index_dir, exist_ok=True)
    if meta_dir:
        os.makedirs(meta_dir, exist_ok=True)
    faiss.write_index(index, index_out)

    with open(meta_out, "wb") as f:
        pickle.dump(records, f)

    print("✅ Multimodal index built!")
    print(f"📁 Saved: {index_out}")
    print(f"📁 Saved: {meta_out}")


# -----------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build multimodal FAISS index from one PDF + figures JSON.")
    parser.add_argument("--pdf", default=PDF_PATH, help="Path to source PDF (text chunks).")
    parser.add_argument("--figures-json", default=FIGURES_JSON, help="Path to structured figures JSON.")
    parser.add_argument("--index-out", default=INDEX_PATH, help="Output FAISS index path.")
    parser.add_argument("--meta-out", default=META_PATH, help="Output metadata pickle path.")
    args = parser.parse_args()

    build_index(
        pdf_path=args.pdf,
        figures_json=args.figures_json,
        index_out=args.index_out,
        meta_out=args.meta_out,
    )

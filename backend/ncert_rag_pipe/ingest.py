import os
import faiss
import pickle
import json
from pathlib import Path
import fitz  # This is PyMuPDF
from sentence_transformers import SentenceTransformer
from config import MASTER_CHAPTER_MAP

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BOOKS_ROOT = os.path.join(PROJECT_ROOT, "NCERT_Books")
INDEXES_DIR = os.path.join(PROJECT_ROOT, "indexes")
MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 700 

model = SentenceTransformer(MODEL_NAME)

def run_ingestion():
    runtime_mapping = {}

    if not os.path.exists(BOOKS_ROOT):
        print(f"Error: {BOOKS_ROOT} not found.")
        return

    for folder_name in os.listdir(BOOKS_ROOT):
        folder_path = os.path.join(BOOKS_ROOT, folder_name)
        if not os.path.isdir(folder_path): continue

        parts = folder_name.split('_')
        if len(parts) != 3: continue
        
        lang, std, sub = parts
        print(f"📦 Indexing Folder: {folder_name}")

        try:
            topic_list = MASTER_CHAPTER_MAP[lang][sub][std]
        except KeyError:
            topic_list = []

        for file in sorted(os.listdir(folder_path), key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else 0):
            if not file.endswith(".pdf"): continue
            
            idx_str = Path(file).stem
            idx_int = int(idx_str) - 1
            topic_name = topic_list[idx_int] if idx_int < len(topic_list) else f"Chapter {idx_str}"
            
            out_dir = os.path.join(INDEXES_DIR, lang, std, sub, idx_str)
            os.makedirs(out_dir, exist_ok=True)

            # --- NEW PDF EXTRACTION LOGIC ---
            full_text = ""
            try:
                with fitz.open(os.path.join(folder_path, file)) as doc:
                    for page in doc:
                        full_text += page.get_text()
            except Exception as e:
                print(f"   ❌ Failed to read {file}: {e}")
                continue

            # Semantic Processing (Same as before)
            words = full_text.split()
            chunks = [" ".join(words[i:i + CHUNK_SIZE]) for i in range(0, len(words), CHUNK_SIZE)]
            
            if not chunks: continue # Skip empty chapters

            embeddings = model.encode(chunks)
            index = faiss.IndexFlatL2(embeddings.shape[1])
            index.add(embeddings)

            # Save per-chapter DB
            faiss.write_index(index, os.path.join(out_dir, "vector_db.index"))
            with open(os.path.join(out_dir, "data.pkl"), "wb") as f:
                pickle.dump({"title": topic_name, "full_text": full_text, "chunks": chunks}, f)

            # Update mapping (Same as before)
            sub_map = runtime_mapping.setdefault(lang, {}).setdefault(std, {}).setdefault(sub, {})
            sub_map[idx_str] = topic_name
            sub_map[topic_name] = idx_str
            print(f"   ✅ {idx_str}.pdf -> {topic_name}")

    with open(os.path.join(INDEXES_DIR, "chapter_map.json"), "w", encoding="utf-8") as f:
        json.dump(runtime_mapping, f, ensure_ascii=False, indent=4)
    print("\n✅ Ingestion finished. All indexes created.")

if __name__ == "__main__":
    run_ingestion()
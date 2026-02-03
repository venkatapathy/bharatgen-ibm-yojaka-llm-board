import os
import faiss
import pickle
import json
from sentence_transformers import SentenceTransformer

# Use the directory where the script is located
FILE_PATH = os.path.abspath(__file__)

# 2. This is the folder the script is in (e.g., .../backend/ncert_rag_pipe)
BASE_DIR = os.path.dirname(FILE_PATH)

# 3. Climb up to get to the project root
# Go up 1 level to 'backend'
BACKEND_DIR = os.path.dirname(BASE_DIR)
# Go up another level to the root (where 'indexes' lives)
BOOKS_DIR = os.path.join(BASE_DIR, "NCERT_Books")

INDEXES_DIR = os.path.join(BASE_DIR, "indexes")

class NCERTServer:
    def __init__(self):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        # Load the chapter map created by ingest.py
        map_path = os.path.join(INDEXES_DIR, "chapter_map.json")
        with open(map_path, "r", encoding="utf-8") as f:
            self.mapping = json.load(f)

    def _find_location(self, lang, chapter_input):
        """Locates the Std and Subject for a given chapter title."""
        lang_data = self.mapping.get(lang, {})
        for std, subjects in lang_data.items():
            for sub, chapters in subjects.items():
                if str(chapter_input) in chapters:
                    return std, sub
        return None, None

    def get_context(self, lang, chapter_input, query):
        std, sub = self._find_location(lang, chapter_input)
        if not std: return None

        # Resolve "Real Numbers" -> "1"
        lookup = self.mapping[lang][std][sub]
        val = lookup.get(str(chapter_input))
        idx = val if (val and val.isdigit()) else str(chapter_input)

        chapter_path = os.path.join(INDEXES_DIR, lang, std, sub, idx)
        
        # Load specific chapter DB created by ingest.py
        index = faiss.read_index(os.path.join(chapter_path, "vector_db.index"))
        with open(os.path.join(chapter_path, "data.pkl"), "rb") as f:
            data = pickle.load(f)

        # Search
        query_vec = self.model.encode([query])
        distances, indices = index.search(query_vec, k=3)
        
        retrieved_chunks = [data["chunks"][i] for i in indices[0] if i != -1]
        metadata = [{"source_path": f"{lang}_{std}_{sub}/{idx}.pdf", "topic": data["title"]} for _ in retrieved_chunks]

        return retrieved_chunks, metadata, data["full_text"]

# Global Instance
_server = None

def main(chapter, topic, language="en"):
    global _server
    if _server is None: _server = NCERTServer()

    lang_name = "English" if language == "en" else "Hindi"
    
    # Perform the retrieval
    chunks, meta, full_text = _server.get_context(lang_name, chapter, topic)
    
    # Return in the format your pipeline expects
    return full_text, "\n\n".join(chunks), meta, meta
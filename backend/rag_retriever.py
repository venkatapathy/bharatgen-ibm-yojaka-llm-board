import json
import os
import pickle
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv
import numpy as np
from sentence_transformers import SentenceTransformer

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
DEFAULT_K = 5
MIN_CHUNK_WORDS = 5
MAX_CHUNK_WORDS = 5000
DEFAULT_MAX_DROPOFF_PCT = 0.20
DEFAULT_MAX_GRAPH_NEIGHBORS = 6


def _detect_text_language(text: str) -> str:
    """
    Detect language of text. Returns 'hi' for Hindi (Devanagari), 'en' for English, 'unknown' otherwise.
    Uses simple heuristic: checks for Unicode ranges.
    """
    if not text:
        return "unknown"
    
    devanagari_pattern = re.compile(r"[\u0900-\u097F]")
    english_pattern = re.compile(r"[A-Za-z]")
    
    has_devanagari = bool(devanagari_pattern.search(text))
    has_english = bool(english_pattern.search(text))
    
    if has_devanagari:
        return "hi"
    if has_english:
        return "en"
    return "unknown"


def _extract_block_label_from_text(text: str) -> Optional[str]:
    """Extract `Block <name>` label from arbitrary text/path segment."""
    if not text:
        return None

    normalized = re.sub(r"\s+", " ", str(text)).strip()
    match = re.search(r"(?i)\bblock\b[\s:\-]*([a-z0-9]+(?:[\s\-]+[a-z0-9]+){0,6})\b", normalized)
    if not match:
        return None

    captured = re.split(r"(?i)\b(unit|chapter|structure|objectives|introduction)\b", match.group(1))[0].strip()
    if not captured:
        return None

    first_token = captured.split()[0].lower()
    if first_token in {"here", "there", "this", "that", "the", "a", "an", "of", "for", "to", "in"}:
        return None

    return f"Block {captured}"


class MinimalRAGRetriever:
    def __init__(self, store_dir: Path):
        if not store_dir.exists():
            raise FileNotFoundError(f"RAG store not found: {store_dir}")

        self.store_dir = store_dir
        self.model = SentenceTransformer(MODEL_NAME)
        self.master = self._load_master()
        self.records = self._build_records()
        self.cache: Dict[str, Dict[str, Any]] = {}

    def _load_master(self) -> Dict[str, Any]:
        master_path = self.store_dir / "master_index.json"
        if not master_path.exists():
            raise FileNotFoundError(f"master_index.json not found in: {self.store_dir}")
        with open(master_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _resolve_doc_path(self, doc_id: str, metadata: Dict[str, Any]) -> Optional[Path]:
        store_path = str(metadata.get("store_path", "")).strip()
        if store_path:
            p = Path(store_path)
            if not p.is_absolute():
                p = (PROJECT_ROOT / p).resolve()
            if p.exists():
                return p

        matches = list(self.store_dir.rglob(doc_id))
        return matches[0] if matches else None


    def _extract_subject_and_block(self, store_path: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract subject (course name) and block label from store_path."""
        if not store_path:
            return None, None

        normalized = str(store_path).replace("\\", "/")
        parts = [p for p in normalized.split("/") if p and p.lower() != "egyankosh"]

        subject = None
        block = None

        # Iterate through path segments. If a folder has "block X", it's the block.
        # The folder immediately before it is the course/subject.
        for idx, part in enumerate(parts):
            # Match folders that include the word 'block' (numeric or named blocks)
            if re.search(r"(?i)\bblock\b", part):
                block = part  # Keep full name (e.g., "BLOCK KALIDASA ABHIJNANA SHAKUNTALA")
                # The folder immediately before the block folder is the subject/course
                if idx > 0 and not parts[idx - 1].lower().startswith("rag_store"):
                    subject = parts[idx - 1]
                break

        # Fallback if no explicit block detected: assume the second-last segment is the subject
        if not subject and len(parts) >= 2:
            subject = parts[-2]

        return subject, block

    def _build_records(self) -> List[Dict[str, Any]]:
        docs = self.master.get("documents", {})
        records: List[Dict[str, Any]] = []

        for doc_id, meta in docs.items():
            doc_dir = self._resolve_doc_path(doc_id, meta)
            if not doc_dir:
                continue

            chunks_path = doc_dir / "chunks.json"
            embeddings_path = doc_dir / "embeddings.npy"
            metadata_path = doc_dir / "metadata.json"
            if not (chunks_path.exists() and embeddings_path.exists() and metadata_path.exists()):
                continue

            searchable = " ".join(
                [
                    doc_id,
                    str(meta.get("file_name", "")),
                    str(meta.get("store_path", "")),
                    doc_dir.as_posix(),
                ]
            ).lower()

            store_path = str(meta.get("store_path", ""))
            subject, block = self._extract_subject_and_block(store_path)

            records.append(
                {
                    "doc_id": doc_id,
                    "doc_dir": doc_dir,
                    "meta": meta,
                    "search_text": searchable,
                    "extracted_subject": subject,
                    "extracted_block": block,
                }
            )

        if not records:
            raise FileNotFoundError(
                f"No usable unit folders found under {self.store_dir}. "
                "Expected chunks.json + embeddings.npy + metadata.json per document."
            )

        return records

    def _candidate_records(
        self,
        query: str,
        subject: Optional[str],
        chapter: Optional[str],
        standard: Optional[str],
        block: Optional[str],
        language: Optional[str] = None,
        max_candidates: int = 40,
    ) -> List[Dict[str, Any]]:
        filtered_records = self.records

        effective_block = block
        if not effective_block and chapter:
            # Check if chapter string passed from UI holds the block
            if re.search(r"(?i)\bblock\b", chapter):
                effective_block = chapter

        had_explicit_filters = bool(subject or effective_block)
        print(f"DEBUG RAG: Searching for subject='{subject}', block='{effective_block}'")
        print(f"DEBUG RAG: Total potential records in index: {len(self.records)}")

        if subject:
            subject_lower = subject.lower()
            filtered_records = [
                r for r in filtered_records
                if r["extracted_subject"] and subject_lower in r["extracted_subject"].lower()
            ]
            print(f"DEBUG RAG: Records after subject filter: {len(filtered_records)}")

        if effective_block:
            # Ensure exact matching so "Block 1" doesn't retrieve "Block 10"
            target_match = re.search(r"(?i)\bblock\s*(\d+)", effective_block)
            if target_match:
                b_num = target_match.group(1)
                # Regex boundary \b ensures it matches exactly the number
                pattern = rf"(?i)\bblock\s*0*{b_num}\b"
                filtered_records = [
                    r for r in filtered_records
                    if r["extracted_block"] and re.search(pattern, r["extracted_block"])
                ]
            else:
                block_lower = effective_block.lower()
                filtered_records = [
                    r for r in filtered_records
                    if r["extracted_block"] and block_lower in r["extracted_block"].lower()
                ]
            print(f"DEBUG RAG: Records after block filter: {len(filtered_records)}")

        # RESTORED MISSING RETURN STATEMENTS:
        if not filtered_records and had_explicit_filters:
            print(f"DEBUG RAG: No candidates found matching filters.")
            return []

        if not filtered_records:
            filtered_records = self.records

        return filtered_records[:max_candidates]

    def _load_doc_payload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = record["doc_id"]
        if doc_id in self.cache:
            return self.cache[doc_id]

        doc_dir: Path = record["doc_dir"]
        with open(doc_dir / "chunks.json", "r", encoding="utf-8") as f:
            chunks = json.load(f)
        embeddings = np.load(doc_dir / "embeddings.npy")

        if not isinstance(chunks, list):
            raise ValueError(f"chunks.json in {doc_dir} must be a list")
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)
        if embeddings.shape[0] != len(chunks):
            raise ValueError(
                f"Chunk/embedding mismatch in {doc_dir}: chunks={len(chunks)}, embeddings={embeddings.shape[0]}"
            )

        graph = None
        graph_path = doc_dir / "graph.pkl"
        if graph_path.exists():
            try:
                with open(graph_path, "rb") as f:
                    graph = pickle.load(f)
            except Exception:
                graph = None

        chunk_lookup: Dict[str, Dict[str, Any]] = {}
        for chunk in chunks:
            if isinstance(chunk, dict):
                cid = str(chunk.get("chunk_id", "")).strip()
                if cid:
                    chunk_lookup[cid] = chunk

        payload = {
            "chunks": chunks,
            "embeddings": embeddings,
            "graph": graph,
            "chunk_lookup": chunk_lookup,
        }
        self.cache[doc_id] = payload
        return payload

    def _chunk_text(self, chunk: Any) -> str:
        if isinstance(chunk, dict):
            return str(chunk.get("text", ""))
        if isinstance(chunk, str):
            return chunk
        return ""

    def _word_count(self, text: str) -> int:
        return len((text or "").split())

    def _filter_results_by_language(
        self,
        ranked: List[Tuple[float, str, Dict[str, Any]]],
        language: Optional[str],
    ) -> List[Tuple[float, str, Dict[str, Any]]]:
        """
        Filter ranked results by language. If language is specified, keep only chunks matching that language.
        If language is 'unknown' or not set, return all results.
        """
        if not language or language.lower() == "unknown":
            return ranked
        
        target_lang = language.lower()
        filtered = []
        
        for score, text, meta in ranked:
            detected_lang = _detect_text_language(text)
            if detected_lang == target_lang or detected_lang == "unknown":
                filtered.append((score, text, meta))
        
        return filtered if filtered else ranked  # If filtering removes everything, return original

    def _apply_dynamic_dropoff(
        self,
        ranked: List[Tuple[float, str, Dict[str, Any]]],
        max_drop_pct: float = DEFAULT_MAX_DROPOFF_PCT,
    ) -> List[Tuple[float, str, Dict[str, Any]]]:
        if not ranked:
            return ranked

        safe_drop_pct = min(max(float(max_drop_pct), 0.0), 0.95)
        best_score = ranked[0][0]
        if best_score <= 0:
            return ranked[:1]

        filtered: List[Tuple[float, str, Dict[str, Any]]] = []
        for item in ranked:
            score = item[0]
            drop_pct = (best_score - score) / best_score
            if drop_pct > safe_drop_pct and filtered:
                break
            filtered.append(item)
        return filtered

    def _expand_context_with_graph(
        self,
        base_text: str,
        meta: Dict[str, Any],
        max_neighbors: int = DEFAULT_MAX_GRAPH_NEIGHBORS,
    ) -> Tuple[str, int]:
        doc_id = str(meta.get("doc_id", "")).strip()
        chunk_id = str(meta.get("chunk_id", "")).strip()
        if not doc_id or not chunk_id:
            return base_text, 0

        payload = self.cache.get(doc_id)
        if not payload:
            return base_text, 0

        graph = payload.get("graph")
        chunk_lookup = payload.get("chunk_lookup") or {}
        if graph is None or chunk_id not in graph:
            return base_text, 0

        neighbors_text: List[str] = []
        seen_ids = {chunk_id}

        def _append_neighbor(neighbor_id: str, label: str) -> None:
            if len(neighbors_text) >= max_neighbors:
                return
            n_id = str(neighbor_id)
            if not n_id or n_id in seen_ids:
                return
            seen_ids.add(n_id)
            n_chunk = chunk_lookup.get(n_id)
            if not isinstance(n_chunk, dict):
                return
            n_text = self._chunk_text(n_chunk).strip()
            wc = self._word_count(n_text)
            if wc <= MIN_CHUNK_WORDS:
                return
            title = str(n_chunk.get("title") or "Untitled")
            neighbors_text.append(f"[{label}: {title}]\n{n_text}")

        try:
            if hasattr(graph, "is_directed") and graph.is_directed():
                for p_id in list(graph.predecessors(chunk_id)):
                    _append_neighbor(p_id, "Broader Context / Parent")
                for c_id in list(graph.successors(chunk_id)):
                    _append_neighbor(c_id, "Deeper Detail / Child")
            else:
                for n_id in list(graph.neighbors(chunk_id)):
                    _append_neighbor(n_id, "Related Context")
        except Exception:
            return base_text, 0

        if not neighbors_text:
            return base_text, 0

        separator = '\n\n'
        expanded = (
            f"{base_text}\n\n"
            "--- Graph Context Expansion (Connected Sections) ---\n"
            f"{separator.join(neighbors_text)}"
        )
        return expanded, len(neighbors_text)

    def retrieve(
        self,
        query: str,
        subject: Optional[str] = None,
        chapter: Optional[str] = None,
        standard: Optional[str] = None,
        block: Optional[str] = None,
        language: Optional[str] = None,
        k: int = DEFAULT_K,
        enable_dynamic_dropoff: bool = True,
        max_drop_pct: float = DEFAULT_MAX_DROPOFF_PCT,
        enable_graph_expansion: bool = False,
        max_graph_neighbors: int = DEFAULT_MAX_GRAPH_NEIGHBORS,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Retrieve k chunks matching the query and filters.
        
        Args:
            query: The retrieval query
            subject: Optional subject/course filter
            chapter: Optional chapter filter
            standard: Optional standard filter (not currently used)
            block: Optional block filter
            language: Optional language filter ('en', 'hi', or None for all)
            k: Number of chunks to retrieve
            enable_dynamic_dropoff: Whether to keep only results near best similarity score
            max_drop_pct: Allowed drop from best similarity before cutoff
            enable_graph_expansion: Whether to append parent/child/neighbor chunks from graph
            max_graph_neighbors: Maximum number of related graph neighbors to append per chosen chunk
        
        Returns:
            Tuple of (combined_text, metadata_list)
        """
        query = (query or "").strip()
        if not query:
            return "", []

        query_vec = self.model.encode([query], normalize_embeddings=True)[0]
        candidates = self._candidate_records(query, subject, chapter, standard, block, language)

        ranked: List[Tuple[float, str, Dict[str, Any]]] = []

        for record in candidates:
            payload = self._load_doc_payload(record)
            embeddings = payload["embeddings"]

            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            normalized = embeddings / norms
            sims = normalized @ query_vec

            if sims.size == 0:
                continue

            local_top_n = min(max(10, k * 10), sims.shape[0])
            local_top = np.argsort(sims)[-local_top_n:][::-1]
            for idx in local_top:
                chunk = payload["chunks"][int(idx)]
                text = self._chunk_text(chunk).strip()
                wc = self._word_count(text)
                if wc <= MIN_CHUNK_WORDS or wc >= MAX_CHUNK_WORDS:
                    continue
                
                if isinstance(chunk, dict):
                    title = chunk.get("page", "")
                    if "glossary" in title:
                        continue

                meta = {
                    "doc_id": record["doc_id"],
                    "file_name": record["meta"].get("file_name"),
                    "source_path": record["meta"].get("store_path"),
                    "title": chunk.get("title") if isinstance(chunk, dict) else None,
                    "page": chunk.get("page") if isinstance(chunk, dict) else None,
                    "chunk_id": chunk.get("chunk_id") if isinstance(chunk, dict) else None,
                    "similarity": float(sims[int(idx)]),
                    "word_count": wc,
                }
                ranked.append((float(sims[int(idx)]), text, meta))

        if not ranked:
            return "", []

        # Apply language filtering if specified
        ranked = self._filter_results_by_language(ranked, language)

        ranked.sort(key=lambda item: item[0], reverse=True)
        if enable_dynamic_dropoff:
            ranked = self._apply_dynamic_dropoff(ranked, max_drop_pct=max_drop_pct)

        chosen = ranked[: max(1, k)]
        texts: List[str] = []
        metas: List[Dict[str, Any]] = []
        for score, text, meta in chosen:
            item_meta = dict(meta)
            if enable_graph_expansion:
                expanded_text, related_count = self._expand_context_with_graph(
                    base_text=text,
                    meta=item_meta,
                    max_neighbors=max_graph_neighbors,
                )
                texts.append(expanded_text)
                item_meta["graph_expanded"] = related_count > 0
                item_meta["graph_related_count"] = related_count
            else:
                texts.append(text)
                item_meta["graph_expanded"] = False
                item_meta["graph_related_count"] = 0
            metas.append(item_meta)

        return "\n\n".join(texts), metas

    def _load_citations_from_doc(self, doc_dir: Path) -> List[Dict[str, Any]]:
        # Look for citations.json in the document folder and upward through parents
        found: List[Dict[str, Any]] = []
        tried_paths: List[Path] = []

        # Search doc_dir and its ancestors up to the RAG store root
        search_dirs = [doc_dir] + list(doc_dir.parents)
        for p in search_dirs:
            # Stop searching once we escape the store root to avoid scanning unrelated folders
            try:
                if p == self.store_dir or self.store_dir in p.parents:
                    # allow checking this directory and continue one level up if needed
                    pass
            except Exception:
                # In case of unusual path perms/resolution, continue but limit search depth
                pass

            citations_path = p / "citations.json"
            tried_paths.append(citations_path)
            if not citations_path.exists():
                continue
            try:
                with open(citations_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            entries: List[Dict[str, Any]] = []
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                for key in ("citations", "items", "quotes"):
                    if isinstance(data.get(key), list):
                        entries = data.get(key)
                        break
                if not entries:
                    # If the dict maps ids to objects or simple strings, return values
                    values = [v for v in data.values() if isinstance(v, (dict, str))]
                    if values:
                        entries = values

            # Normalize entries to dicts when possible
            for e in entries:
                if isinstance(e, (dict, str)):
                    found.append(e)

            # If we found entries in a parent block folder, prefer those (don't continue searching further up)
            if found:
                break

        return found

    def _strip_citation_parentheticals(self, text: str) -> str:
        # Remove parenthetical citation markers that likely indicate pages/authors.
        if not text:
            return text
        # Remove parentheses that contain page markers, digits, 'p.', 'pp.', 'page', or typical citation tokens
        pattern = re.compile(r"\s*\((?:[^)]*\b(?:p\.?|pp\.?|page|pg|Prologue|Chapter|Canto|Book)\b[^)]*)\)", flags=re.IGNORECASE)
        cleaned = re.sub(pattern, "", text)
        # Also remove isolated parentheticals that are just numbers like (20)
        cleaned = re.sub(r"\s*\(\s*\d{1,4}\s*\)", "", cleaned)
        return cleaned.strip()

    def _extract_page_from_text(self, text: str) -> Optional[str]:
        if not text:
            return None
        # Look for parenthetical groups that mention page, p., pp., Canto, Book, Prologue, etc.
        m = re.search(r"\(([^)]*(?:\b(?:p\.?|pp\.?|page|pg|canto|book|prologue)\b)[^)]*)\)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback: find 'p' followed by digits
        m2 = re.search(r"\b[pP]\.\s*(\d{1,4})\b", text)
        if m2:
            return m2.group(1)
        return None

    def retrieve_citation(
        self,
        query: str,
        subject: Optional[str] = None,
        chapter: Optional[str] = None,
        standard: Optional[str] = None,
        block: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Retrieve a single random citation entry from candidate documents in the given filters.
        Returns (citation_text, [meta]) or ("", []) if none found.
        """
        candidates = self._candidate_records(query, subject, chapter, standard, block, language)
        all_citations = []
        for record in candidates:
            doc_dir: Path = record["doc_dir"]
            citations = self._load_citations_from_doc(doc_dir)
            for c in citations:
                all_citations.append((record, doc_dir, c))

        if not all_citations:
            return "", []

        import random

        rec, doc_dir, citation = random.choice(all_citations)

        # citation may be a string or dict; support 'citation_text' key used in samples
        if isinstance(citation, dict):
            text = citation.get("citation_text") or citation.get("text") or citation.get("quote") or citation.get("citation") or ""
        else:
            text = str(citation)

        text = str(text or "").strip()
        # Try to extract page/locator info from the raw text before stripping
        page_info = self._extract_page_from_text(text)
        cleaned = self._strip_citation_parentheticals(text)

        meta = {
            "doc_id": rec.get("doc_id"),
            "file_name": rec.get("meta", {}).get("file_name"),
            "source_path": rec.get("meta", {}).get("store_path"),
            "title": None,
            "page": page_info,
            "chunk_id": None,
            "similarity": None,
            "word_count": len(cleaned.split()),
            "citation_original": text,
        }

        return cleaned, [meta]

    def retrieve_dual_citation(
        self,
        topic_query: str,
        theme_query: Optional[str] = None,
        subject: Optional[str] = None,
        chapter: Optional[str] = None,
        standard: Optional[str] = None,
        block: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Tuple[str, str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        theme_query = theme_query or topic_query
        topic_text, topic_meta = self.retrieve_citation(topic_query, subject, chapter, standard, block, language)
        theme_text, theme_meta = self.retrieve_citation(theme_query, subject, chapter, standard, block, language)
        return topic_text, theme_text, topic_meta, theme_meta

    def retrieve_dual(
        self,
        topic_query: str,
        theme_query: Optional[str] = None,
        subject: Optional[str] = None,
        chapter: Optional[str] = None,
        standard: Optional[str] = None,
        block: Optional[str] = None,
        language: Optional[str] = None,
        k: int = DEFAULT_K,
        enable_dynamic_dropoff: bool = True,
        max_drop_pct: float = DEFAULT_MAX_DROPOFF_PCT,
        enable_graph_expansion: bool = False,
        max_graph_neighbors: int = DEFAULT_MAX_GRAPH_NEIGHBORS,
    ) -> Tuple[str, str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Retrieve chunks for both topic and theme (used by council flow).
        
        Args:
            topic_query: Query for topic chunks
            theme_query: Query for theme chunks (optional, defaults to topic_query)
            subject: Optional subject/course filter
            chapter: Optional chapter filter
            standard: Optional standard filter
            block: Optional block filter
            language: Optional language filter
            k: Number of chunks to retrieve for each query
            enable_dynamic_dropoff: Whether to keep only results near best similarity score
            max_drop_pct: Allowed drop from best similarity before cutoff
            enable_graph_expansion: Whether to append parent/child/neighbor chunks from graph
            max_graph_neighbors: Maximum number of related graph neighbors to append per chosen chunk
        
        Returns:
            Tuple of (topic_text, theme_text, topic_metadata, theme_metadata)
        """
        theme_query = theme_query or topic_query
        
        topic_text, topic_meta = self.retrieve(
            query=topic_query,
            subject=subject,
            chapter=chapter,
            standard=standard,
            block=block,
            language=language,
            k=k,
            enable_dynamic_dropoff=enable_dynamic_dropoff,
            max_drop_pct=max_drop_pct,
            enable_graph_expansion=enable_graph_expansion,
            max_graph_neighbors=max_graph_neighbors,
        )
        
        theme_text, theme_meta = self.retrieve(
            query=theme_query,
            subject=subject,
            chapter=chapter,
            standard=standard,
            block=block,
            language=language,
            k=k,
            enable_dynamic_dropoff=enable_dynamic_dropoff,
            max_drop_pct=max_drop_pct,
            enable_graph_expansion=enable_graph_expansion,
            max_graph_neighbors=max_graph_neighbors,
        )
        
        return topic_text, theme_text, topic_meta, theme_meta

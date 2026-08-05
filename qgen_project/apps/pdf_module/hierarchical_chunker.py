"""IGNOU / NCERT-style hierarchical chunker (from Shagnik ncert_rag_pipe).

Splits on UNIT/CHAPTER headers, then decimal sections (1.0, 1.1, 1.2.1…),
preserving parent/child links for RAG.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HierarchicalChunk:
    chunk_id: str
    document_id: str
    text: str
    title: str
    level: int
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["word_count"] = len(self.text.split())
        return data


class HierarchicalChunker:
    def __init__(self):
        self.unit_header_re = re.compile(
            r"(?im)^[ \t]*(?P<marker>(?:unit|chapter)\s+\d+[a-zA-Z0-9-]*)"
            r"(?:[ \t]+(?P<title_inline>[^\n]{1,180})|[ \t]*\n[ \t]*(?P<title_next>[^\n]{1,180}))?"
        )
        self.section_header_re = re.compile(
            r"(?im)^[ \t]*(?P<marker>(?:\d+\.\d+(?:\.\d+)*|"
            r"(?:section|subsection|sub-section)\s+\d+(?:\.\d+)*|"
            r"(?:example|figure|table)\s+\d+(?:\.\d+)?))"
            r"(?:[ \t]+(?P<title_inline>[^\n]{1,180})|"
            r"[ \t]*(?:\n[ \t]*)+(?P<title_next>(?!\d+(?:\.\d+)+\s*$)[^\n]{1,180}))?"
        )
        self.front_matter_re = re.compile(
            r"(?i)(all rights reserved|isbn|printed and published|section officer|"
            r"school of humanities|laser typeset|copyright|block introduction)"
        )
        self.control_chars_re = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\uFFFD]")

    def sanitize_text(self, text: str) -> str:
        text = text or ""
        text = self.control_chars_re.sub(" ", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _is_front_matter_like(self, text: str, title: str = "") -> bool:
        blob = f"{title}\n{text[:1000]}"
        if self.front_matter_re.search(blob):
            if len((text or "").split()) < 150:
                return True
        wc = len((text or "").split())
        if wc < 12 and "structure" in blob.lower():
            return True
        return False

    def _is_toc_like(self, marker: str, section_text: str, title: str) -> bool:
        marker = (marker or "").strip().lower()
        section_text = (section_text or "").strip()
        title = (title or "").strip().lower()

        if "structure" in title and len(section_text.split()) < 60:
            return True

        if marker and re.fullmatch(r"\d+(?:\.\d+)+", marker):
            if not section_text:
                return True
            lines = [line.strip() for line in section_text.splitlines() if line.strip()]
            if lines and all(re.fullmatch(r"\d+(?:\.\d+)+", ln) for ln in lines):
                return True
            if len(section_text.split()) <= 8 and not re.search(r"[a-zA-Z]{3,}", section_text):
                return True
        return False

    def split_into_unit_documents(
        self, full_text: str, base_document_id: str, min_unit_words: int = 80
    ) -> List[dict]:
        text = self.sanitize_text(full_text)
        matches = list(self.unit_header_re.finditer(text))
        if not matches:
            return [{"document_id": base_document_id, "title": "Full Document", "text": text}]

        docs = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            unit_text = text[start:end].strip()
            if len(unit_text.split()) < min_unit_words:
                continue
            marker = match.group("marker").strip()
            title = (match.group("title_inline") or match.group("title_next") or "").strip()
            normalized_marker = re.sub(r"\s+", "_", marker.upper())
            docs.append(
                {
                    "document_id": f"{base_document_id}_{normalized_marker}",
                    "title": f"{marker} {title}".strip(),
                    "text": unit_text,
                }
            )
        return docs or [{"document_id": base_document_id, "title": "Full Document", "text": text}]

    def infer_level(self, marker: str) -> int:
        marker = marker.strip().lower()
        if any(marker.startswith(x) for x in ["chapter", "unit", "part", "module", "section"]):
            return 1
        decimals = re.findall(r"\d+", marker)
        if "." in marker and len(decimals) >= 2:
            return min(len(decimals), 5)
        if any(marker.startswith(x) for x in ["example", "figure", "table"]):
            return 3
        return 2

    def chunk_document(
        self,
        pages: List[str],
        full_text: str,
        document_id: str,
        max_chunk_words: int = 800,
    ) -> List[HierarchicalChunk]:
        cleaned_text = self.sanitize_text(full_text)
        unit_docs = self.split_into_unit_documents(cleaned_text, document_id)
        chunks: List[HierarchicalChunk] = []

        root_chunk = HierarchicalChunk(
            chunk_id=f"{document_id}_root",
            document_id=document_id,
            text="",
            title="Document Root / Introduction",
            level=1,
        )
        chunks.append(root_chunk)

        chunk_counter = 0
        for unit_idx, unit_doc in enumerate(unit_docs):
            unit_text = unit_doc["text"]
            unit_title = unit_doc["title"] or f"Unit {unit_idx + 1}"

            if self._is_front_matter_like(unit_text[:1500], unit_title):
                continue

            unit_chunk_id = f"{document_id}_unit_{unit_idx}"
            unit_chunk = HierarchicalChunk(
                chunk_id=unit_chunk_id,
                document_id=document_id,
                text=unit_text[: min(1200, len(unit_text))],
                title=unit_title,
                level=1,
                parent_id=root_chunk.chunk_id,
            )
            chunks.append(unit_chunk)

            matches = list(self.section_header_re.finditer(unit_text))
            if not matches:
                continue

            last_seen_at_level = {1: unit_chunk_id, 2: None, 3: None, 4: None, 5: None}
            seen_titles: dict = {}

            for i, match in enumerate(matches):
                marker = (match.group("marker") or "").strip()
                title_text = (match.group("title_inline") or match.group("title_next") or "").strip()
                start_idx = match.end()
                end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(unit_text)
                section_text = unit_text[start_idx:end_idx].strip()
                full_title = f"{marker} {title_text}".strip()

                if self._is_toc_like(marker, section_text, full_title):
                    continue
                if self._is_front_matter_like(section_text, full_title):
                    continue

                level = max(2, self.infer_level(marker))
                normalized_title = re.sub(r"\s+", " ", full_title.strip().lower())
                if normalized_title in seen_titles:
                    existing_chunk = seen_titles[normalized_title]
                    if len(section_text.split()) >= 12:
                        existing_chunk.text = (existing_chunk.text + "\n\n" + section_text).strip()
                    continue

                chunk_id = f"{document_id}_sec_{chunk_counter}"
                chunk_counter += 1

                parent_level = level - 1
                while parent_level > 0 and last_seen_at_level.get(parent_level) is None:
                    parent_level -= 1
                parent_id = last_seen_at_level.get(parent_level) or unit_chunk_id

                body_text = section_text
                if len(body_text.split()) > max_chunk_words:
                    words = body_text.split()
                    body_text = " ".join(words[:max_chunk_words])

                chunk = HierarchicalChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    text=f"{full_title}\n\n{body_text}".strip(),
                    title=full_title,
                    level=level,
                    parent_id=parent_id,
                )
                chunks.append(chunk)
                seen_titles[normalized_title] = chunk
                last_seen_at_level[level] = chunk_id
                for deeper_level in range(level + 1, 6):
                    last_seen_at_level[deeper_level] = None

        chunk_dict = {c.chunk_id: c for c in chunks}
        for c in chunks:
            if c.parent_id and c.parent_id in chunk_dict:
                chunk_dict[c.parent_id].children_ids.append(c.chunk_id)

        logger.info(
            "Hierarchical chunking %s → %s chunks",
            document_id,
            len(chunks),
        )
        return chunks


def hierarchical_chunk_texts(
    full_text: str,
    *,
    document_id: str = "doc",
    max_chunk_words: int = 800,
    min_words: int = 30,
) -> List[dict]:
    """Return indexable hierarchical chunks as plain dicts for PDF indexing."""
    chunker = HierarchicalChunker()
    pages = [p for p in (full_text or "").split("\n\n") if p.strip()]
    raw = chunker.chunk_document(pages, full_text or "", document_id, max_chunk_words)
    out: List[dict] = []
    for c in raw:
        text = (c.text or "").strip()
        if len(text.split()) < min_words:
            continue
        out.append(
            {
                "text": text,
                "title": c.title,
                "level": c.level,
                "chunk_id": c.chunk_id,
                "parent_id": c.parent_id,
                "children_ids": list(c.children_ids),
            }
        )
    return out

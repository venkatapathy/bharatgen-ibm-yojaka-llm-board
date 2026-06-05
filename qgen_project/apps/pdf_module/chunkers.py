"""
Chunking strategy registry.
Each chunker returns List[str].
"""
from typing import List


def extract_text_from_pdf(path: str) -> str:
    try:
        import fitz
        doc = fitz.open(path)
        return '\n'.join(page.get_text() for page in doc)
    except ImportError:
        raise RuntimeError('PyMuPDF not installed. Run: pip install PyMuPDF')


def fixed_size_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunks.append(' '.join(words[i:i + chunk_size]))
        i += chunk_size - chunk_overlap
    return chunks


def sentence_chunker(text: str, **kwargs) -> List[str]:
    try:
        import nltk
        try:
            return nltk.sent_tokenize(text)
        except LookupError:
            nltk.download('punkt', quiet=True)
            return nltk.sent_tokenize(text)
    except ImportError:
        # Simple fallback
        return [s.strip() for s in text.split('.') if s.strip()]


def paragraph_chunker(text: str, **kwargs) -> List[str]:
    return [p.strip() for p in text.split('\n\n') if p.strip()]


def recursive_chunker(text: str, chunk_size=512, chunk_overlap=64, **kwargs) -> List[str]:
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return splitter.split_text(text)
    except ImportError:
        return fixed_size_chunker(text, chunk_size, chunk_overlap)


def semantic_chunker(text: str, embed_fn=None, **kwargs) -> List[str]:
    if embed_fn is None:
        return paragraph_chunker(text)
    try:
        from langchain_experimental.text_splitter import SemanticChunker
        splitter = SemanticChunker(embed_fn)
        return splitter.split_text(text)
    except ImportError:
        return paragraph_chunker(text)


STRATEGY_MAP = {
    'fixed_size': fixed_size_chunker,
    'sentence':   sentence_chunker,
    'paragraph':  paragraph_chunker,
    'recursive':  recursive_chunker,
    'semantic':   semantic_chunker,
}

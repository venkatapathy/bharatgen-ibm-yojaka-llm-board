import logging
import os
import re
import tempfile
import zipfile

from celery import shared_task

from apps.core.embeddings import DEFAULT_EMBED_MODEL, embed_texts

from .chunkers import chunk_page_text, extract_pages_from_pdf, is_indexable_chunk
from .hierarchical_chunker import hierarchical_chunk_texts
from .models import PDFChunk, PDFContext

logger = logging.getLogger(__name__)


def get_embed_fn(model_name: str):
    def _embed(texts):
        try:
            return embed_texts(texts, model_name or DEFAULT_EMBED_MODEL)
        except Exception as exc:
            logger.warning("Embedding failed (%s): %s", model_name, exc)
            return [None] * len(texts)

    return _embed


def _iter_pdf_files(context, tmpdir):
    zip_path = context.zip_path.path
    if zip_path.endswith(".zip"):
        with zipfile.ZipFile(zip_path, "r") as archive:
            for member in archive.namelist():
                destination = os.path.realpath(os.path.join(tmpdir, member))
                if not destination.startswith(os.path.realpath(tmpdir)):
                    raise ValueError(f"Path traversal detected: {member}")
            archive.extractall(tmpdir)
        for root, _, files in os.walk(tmpdir):
            for filename in files:
                if filename.lower().endswith(".pdf"):
                    yield os.path.join(root, filename)
    else:
        yield zip_path


EMBED_BATCH_SIZE = 64


def _doc_id_from_path(pdf_path: str) -> str:
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    safe = re.sub(r"[^\w\-]+", "_", stem).strip("_")
    return (safe or "doc")[:80]


def _build_chunks(context):
    embed_fn = get_embed_fn(context.embed_model)
    chunks_to_create = []
    pending_texts = []
    pending_meta = []
    embedded_count = 0
    strategy = (context.strategy or "hierarchical").strip()
    max_words = max(int(context.chunk_size or 512), 200)

    def flush_batch():
        nonlocal embedded_count
        if not pending_texts:
            return
        embeddings = (
            embed_fn(pending_texts)
            if context.has_embedding
            else [None] * len(pending_texts)
        )
        for meta, text, embedding in zip(pending_meta, pending_texts, embeddings):
            if embedding is not None:
                embedded_count += 1
            chunks_to_create.append(
                PDFChunk(
                    context=context,
                    source_file=meta["source_file"],
                    page_number=meta.get("page_number"),
                    chunk_index=meta["chunk_index"],
                    text=text,
                    embedding=embedding,
                    token_count=len(text.split()),
                    metadata=meta.get("metadata") or {"strategy": strategy},
                )
            )
        pending_texts.clear()
        pending_meta.clear()
        context.embedded_chunk_count = embedded_count
        context.save(update_fields=["embedded_chunk_count"])

    def enqueue(text, *, source_file, page_number, chunk_index, metadata):
        if not text or not is_indexable_chunk(text, min_tokens=20):
            return
        pending_texts.append(text)
        pending_meta.append(
            {
                "source_file": source_file,
                "page_number": page_number,
                "chunk_index": chunk_index,
                "metadata": metadata,
            }
        )
        if len(pending_texts) >= EMBED_BATCH_SIZE:
            flush_batch()

    with tempfile.TemporaryDirectory() as tmpdir:
        ocr_parts: list[str] = []
        for pdf_path in _iter_pdf_files(context, tmpdir):
            pages = extract_pages_from_pdf(pdf_path)
            source_file = os.path.basename(pdf_path)
            chunk_index = 0

            page_texts = []
            for page in pages:
                text = (page.get("text") or "").strip()
                if not text:
                    continue
                pn = page.get("page_number")
                if pn:
                    page_texts.append(f"===== PAGE {pn} =====\n{text}")
                else:
                    page_texts.append(text)
            if page_texts:
                ocr_parts.append("\n\n".join(page_texts))

            if strategy == "hierarchical" and pages:
                full_text = "\n\n".join(p["text"] for p in pages if p.get("text"))
                hier = hierarchical_chunk_texts(
                    full_text,
                    document_id=_doc_id_from_path(pdf_path),
                    max_chunk_words=max_words,
                    min_words=25,
                )
                if hier:
                    for item in hier:
                        enqueue(
                            item["text"],
                            source_file=source_file,
                            page_number=None,
                            chunk_index=chunk_index,
                            metadata={
                                "strategy": "hierarchical",
                                "title": item.get("title") or "",
                                "level": item.get("level"),
                                "chunk_id": item.get("chunk_id"),
                                "parent_id": item.get("parent_id"),
                            },
                        )
                        chunk_index += 1
                    continue
                logger.warning(
                    "Hierarchical chunking empty for %s; falling back to page chunkers",
                    source_file,
                )

            for page in pages:
                page_chunks = chunk_page_text(
                    page["text"],
                    "fixed_size" if strategy == "hierarchical" else strategy,
                    chunk_size=context.chunk_size,
                    chunk_overlap=context.chunk_overlap,
                    embed_fn=embed_fn,
                )
                for text in page_chunks:
                    enqueue(
                        text,
                        source_file=page["source_file"],
                        page_number=page["page_number"],
                        chunk_index=chunk_index,
                        metadata={"strategy": strategy},
                    )
                    chunk_index += 1
        flush_batch()
    ocr_text = "\n\n".join(ocr_parts).strip()
    return chunks_to_create, embedded_count, ocr_text


@shared_task(bind=True, max_retries=3)
def index_pdf_context(self, context_id: str):
    context = PDFContext.objects.get(id=context_id)
    context.status = "processing"
    context.error_message = ""
    context.save(update_fields=["status", "error_message"])

    try:
        chunks_to_create, embedded_count, ocr_text = _build_chunks(context)
        PDFChunk.objects.filter(context=context).delete()
        PDFChunk.objects.bulk_create(chunks_to_create, batch_size=200)
        from apps.pdf_module.legacy_hindi import looks_like_legacy_hindi, normalize_legacy_hindi

        ocr_text = ocr_text or ""
        is_leg, ft = looks_like_legacy_hindi(ocr_text)
        if is_leg:
            ocr_text = normalize_legacy_hindi(
                ocr_text, force=True, font_type=ft or "krutidev"
            )
        context.ocr_text = ocr_text
        if not chunks_to_create and not ocr_text:
            context.status = "error"
            context.error_message = (
                "No indexable text extracted (OCR/text layer empty). "
                "Re-index after fixing OCR."
            )
            context.needs_reindex = True
            context.embedded_chunk_count = 0
            context.save(
                update_fields=[
                    "status",
                    "error_message",
                    "needs_reindex",
                    "embedded_chunk_count",
                    "ocr_text",
                ]
            )
            logger.error("Empty index for PDFContext %s", context_id)
            return
        context.status = "ready"
        context.needs_reindex = False
        context.error_message = ""
        context.embedded_chunk_count = embedded_count
        context.save(
            update_fields=[
                "status",
                "needs_reindex",
                "embedded_chunk_count",
                "error_message",
                "ocr_text",
            ]
        )
        if context.created_by_id:
            from apps.core.storage import recompute_vector_storage

            recompute_vector_storage(context.created_by)

    except Exception as exc:
        context.status = "error"
        context.error_message = str(exc)
        context.save(update_fields=["status", "error_message"])
        raise self.retry(exc=exc, countdown=30)


@shared_task
def reindex_stale_contexts():
    for context in PDFContext.objects.filter(needs_reindex=True):
        index_pdf_context.delay(str(context.id))

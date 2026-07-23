import logging
import os
import tempfile
import zipfile

from celery import shared_task

from apps.core.embeddings import DEFAULT_EMBED_MODEL, embed_texts

from .models import PDFContext, PDFChunk
from .chunkers import chunk_page_text, extract_pages_from_pdf

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


def _build_chunks(context):
    embed_fn = get_embed_fn(context.embed_model)
    chunks_to_create = []
    pending_texts = []
    pending_meta = []
    embedded_count = 0

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
                    page_number=meta["page_number"],
                    chunk_index=meta["chunk_index"],
                    text=text,
                    embedding=embedding,
                    token_count=len(text.split()),
                    metadata={"strategy": context.strategy},
                )
            )
        pending_texts.clear()
        pending_meta.clear()
        context.embedded_chunk_count = embedded_count
        context.save(update_fields=["embedded_chunk_count"])

    with tempfile.TemporaryDirectory() as tmpdir:
        for pdf_path in _iter_pdf_files(context, tmpdir):
            for page in extract_pages_from_pdf(pdf_path):
                page_chunks = chunk_page_text(
                    page["text"],
                    context.strategy,
                    chunk_size=context.chunk_size,
                    chunk_overlap=context.chunk_overlap,
                    embed_fn=embed_fn,
                )
                for index, text in enumerate(page_chunks):
                    pending_texts.append(text)
                    pending_meta.append(
                        {
                            "source_file": page["source_file"],
                            "page_number": page["page_number"],
                            "chunk_index": index,
                        }
                    )
                    if len(pending_texts) >= EMBED_BATCH_SIZE:
                        flush_batch()
        flush_batch()
    return chunks_to_create, embedded_count


@shared_task(bind=True, max_retries=3)
def index_pdf_context(self, context_id: str):
    context = PDFContext.objects.get(id=context_id)
    context.status = "processing"
    context.error_message = ""
    context.save(update_fields=["status", "error_message"])

    try:
        chunks_to_create, embedded_count = _build_chunks(context)
        PDFChunk.objects.filter(context=context).delete()
        PDFChunk.objects.bulk_create(chunks_to_create, batch_size=200)
        context.status = "ready"
        context.needs_reindex = False
        context.embedded_chunk_count = embedded_count
        context.save(update_fields=["status", "needs_reindex", "embedded_chunk_count", "error_message"])
        if context.created_by_id:
            from apps.core.storage import recompute_vector_storage

            recompute_vector_storage(context.created_by)

    except Exception as exc:
        context.status = 'error'
        context.error_message = str(exc)
        context.save(update_fields=['status', 'error_message'])
        raise self.retry(exc=exc, countdown=30)


@shared_task
def reindex_stale_contexts():
    for context in PDFContext.objects.filter(needs_reindex=True):
        index_pdf_context.delay(str(context.id))

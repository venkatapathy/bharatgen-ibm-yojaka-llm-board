import os
import zipfile
import tempfile
import logging

from celery import shared_task
from .models import PDFContext, PDFChunk
from .chunkers import STRATEGY_MAP, extract_text_from_pdf

logger = logging.getLogger(__name__)


def get_embed_fn(model_name: str):
    """Return a callable that embeds a list of texts."""
    def _embed(texts):
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(model_name)
            return model.encode(texts).tolist()
        except Exception as exc:
            logger.warning('Embedding failed (%s): %s — returning None', model_name, exc)
            return [None] * len(texts)
    return _embed


@shared_task(bind=True, max_retries=3)
def index_pdf_context(self, context_id: str):
    context = PDFContext.objects.get(id=context_id)
    context.status = 'processing'
    context.save(update_fields=['status'])

    try:
        chunker_fn = STRATEGY_MAP[context.strategy]
        embed_fn   = get_embed_fn(context.embed_model)
        chunks_to_create = []

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = context.zip_path.path

            if zip_path.endswith('.zip'):
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    # Guard against path traversal
                    for member in zf.namelist():
                        dest = os.path.realpath(os.path.join(tmpdir, member))
                        if not dest.startswith(os.path.realpath(tmpdir)):
                            raise ValueError(f'Path traversal detected: {member}')
                    zf.extractall(tmpdir)
                pdf_files = [f for f in os.listdir(tmpdir) if f.lower().endswith('.pdf')]
            else:
                # Single PDF
                pdf_files = [os.path.basename(zip_path)]
                tmpdir    = os.path.dirname(zip_path)

            for fname in pdf_files:
                full_path = os.path.join(tmpdir, fname)
                text      = extract_text_from_pdf(full_path)
                raw_chunks = chunker_fn(
                    text,
                    chunk_size=context.chunk_size,
                    chunk_overlap=context.chunk_overlap,
                )
                embeddings = embed_fn(raw_chunks)

                for i, (txt, emb) in enumerate(zip(raw_chunks, embeddings)):
                    chunks_to_create.append(PDFChunk(
                        context=context,
                        source_file=fname,
                        chunk_index=i,
                        text=txt,
                        embedding=emb,
                        token_count=len(txt.split()),
                    ))

        PDFChunk.objects.filter(context=context).delete()
        PDFChunk.objects.bulk_create(chunks_to_create, batch_size=200)
        context.status = 'ready'
        context.save(update_fields=['status', 'error_message'])

    except Exception as exc:
        context.status = 'error'
        context.error_message = str(exc)
        context.save(update_fields=['status', 'error_message'])
        raise self.retry(exc=exc, countdown=30)

# --modified-- START (2026-06-13: pgvector embedding fix — NEW FILE)
# Earlier: no migration; PDFChunk.embedding remained PostgreSQL text from 0001_initial.
# Changed: enable pgvector extension, clear non-vector embeddings, alter column to vector(1536).
# Action: re-index PDF contexts after applying this migration.

from django.db import migrations
from pgvector.django import VectorExtension, VectorField

from apps.pdf_module.models import DEFAULT_EMBED_DIMENSIONS


def clear_text_embeddings(apps, schema_editor):
    """Text values cannot be cast to vector; wipe and require re-index."""
    PDFChunk = apps.get_model("pdf_module", "PDFChunk")
    PDFContext = apps.get_model("pdf_module", "PDFContext")

    if PDFChunk.objects.exists():
        PDFChunk.objects.all().update(embedding=None)
        PDFContext.objects.filter(status="ready").update(status="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("pdf_module", "0001_initial"),
    ]

    operations = [
        VectorExtension(),
        migrations.RunPython(clear_text_embeddings, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pdfchunk",
            name="embedding",
            field=VectorField(dimensions=DEFAULT_EMBED_DIMENSIONS, null=True, blank=True),
        ),
    ]
# --modified-- END

# --modified-- START (2026-06-26: 1536 → 384 for all-MiniLM-L6-v2)

from django.db import migrations
from pgvector.django import VectorField

from apps.pdf_module.models import DEFAULT_EMBED_DIMENSIONS


def clear_embeddings(apps, schema_editor):
    PDFChunk = apps.get_model("pdf_module", "PDFChunk")
    PDFChunk.objects.all().update(embedding=None)


class Migration(migrations.Migration):

    dependencies = [
        ("pdf_module", "0002_alter_pdfchunk_embedding_vectorfield"),
    ]

    operations = [
        migrations.RunPython(clear_embeddings, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pdfchunk",
            name="embedding",
            field=VectorField(dimensions=DEFAULT_EMBED_DIMENSIONS, null=True, blank=True),
        ),
    ]
# --modified-- END

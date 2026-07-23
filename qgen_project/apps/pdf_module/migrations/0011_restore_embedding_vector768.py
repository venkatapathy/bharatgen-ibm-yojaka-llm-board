# Restore PDFChunk.embedding to pgvector vector(768).
# Migration 0010 altered this back to TextField, which breaks L2Distance RAG.

from django.db import migrations
from pgvector.django import VectorField, HnswIndex


class Migration(migrations.Migration):

    dependencies = [
        ("pdf_module", "0010_pdfcontext_embedded_chunk_count_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE pdf_module_pdfchunk
                  ALTER COLUMN embedding TYPE vector(768)
                  USING CASE
                    WHEN embedding IS NULL OR btrim(embedding) = '' THEN NULL
                    ELSE embedding::vector
                  END;
            """,
            reverse_sql="""
                ALTER TABLE pdf_module_pdfchunk
                  ALTER COLUMN embedding TYPE text
                  USING embedding::text;
            """,
        ),
        migrations.AlterField(
            model_name="pdfchunk",
            name="embedding",
            field=VectorField(blank=True, dimensions=768, null=True),
        ),
        migrations.AddIndex(
            model_name="pdfchunk",
            index=HnswIndex(
                ef_construction=64,
                fields=["embedding"],
                m=16,
                name="pdfchunk_embedding_hnsw_idx",
                opclasses=["vector_cosine_ops"],
            ),
        ),
    ]

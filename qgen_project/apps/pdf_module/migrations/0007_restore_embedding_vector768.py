from django.db import migrations
from pgvector.django import VectorField


class Migration(migrations.Migration):

    dependencies = [
        ("pdf_module", "0006_remove_pdfchunk_pdfchunk_embedding_hnsw_idx_and_more"),
        ("core", "0002_tokenusagelog"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE pdf_module_pdfchunk SET embedding = NULL WHERE embedding IS NOT NULL;
                ALTER TABLE pdf_module_pdfchunk
                  ALTER COLUMN embedding TYPE vector(768)
                  USING NULL;
            """,
            reverse_sql="""
                ALTER TABLE pdf_module_pdfchunk
                  ALTER COLUMN embedding TYPE text
                  USING embedding::text;
            """,
            state_operations=[
                migrations.AlterField(
                    model_name="pdfchunk",
                    name="embedding",
                    field=VectorField(dimensions=768, null=True, blank=True),
                ),
            ],
        ),
    ]

# --modified-- START (2026-06-26)
# Django AlterField did not change pgvector column size; use explicit SQL.

from django.db import migrations
from pgvector.django import VectorField


class Migration(migrations.Migration):

    dependencies = [
        ("pdf_module", "0003_alter_pdfchunk_embedding_384"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE pdf_module_pdfchunk SET embedding = NULL WHERE embedding IS NOT NULL;
                ALTER TABLE pdf_module_pdfchunk
                  ALTER COLUMN embedding TYPE vector(384);
            """,
            reverse_sql="""
                UPDATE pdf_module_pdfchunk SET embedding = NULL WHERE embedding IS NOT NULL;
                ALTER TABLE pdf_module_pdfchunk
                  ALTER COLUMN embedding TYPE vector(1536);
            """,
            state_operations=[
                migrations.AlterField(
                    model_name="pdfchunk",
                    name="embedding",
                    field=VectorField(dimensions=384, null=True, blank=True),
                ),
            ],
        ),
    ]
# --modified-- END

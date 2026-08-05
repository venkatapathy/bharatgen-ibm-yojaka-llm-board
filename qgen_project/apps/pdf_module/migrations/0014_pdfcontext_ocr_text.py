# Generated manually for OCR text storage on PDFContext

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pdf_module", "0013_alter_pdfcontext_strategy"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdfcontext",
            name="ocr_text",
            field=models.TextField(blank=True, default=""),
        ),
    ]

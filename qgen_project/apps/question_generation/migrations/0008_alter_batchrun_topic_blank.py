from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("question_generation", "0007_human_review_dataset"),
    ]

    operations = [
        migrations.AlterField(
            model_name="batchrun",
            name="topic",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Deprecated for IGNOU unit-OCR generation; kept for older runs.",
                max_length=512,
            ),
        ),
    ]

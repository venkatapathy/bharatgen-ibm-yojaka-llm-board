from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("question_generation", "0005_remove_batchrun_progress_current_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="batchrun",
            name="language",
            field=models.CharField(
                choices=[("en", "English"), ("hi", "Hindi")],
                default="en",
                help_text="Output language for generated questions and answers.",
                max_length=8,
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("question_generation", "0008_alter_batchrun_topic_blank"),
    ]

    operations = [
        migrations.AddField(
            model_name="batchrunitem",
            name="answer_length",
            field=models.CharField(
                blank=True,
                default="",
                help_text='Target model-answer length for the SPEC, e.g. "100-150 words".',
                max_length=64,
            ),
        ),
    ]

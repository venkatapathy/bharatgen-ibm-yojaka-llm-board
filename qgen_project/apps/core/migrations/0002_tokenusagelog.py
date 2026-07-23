from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("question_generation", "0001_initial"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TokenUsageLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("prompt_tokens", models.IntegerField(default=0)),
                ("completion_tokens", models.IntegerField(default=0)),
                ("embedding_tokens", models.IntegerField(default=0)),
                ("total_tokens", models.IntegerField(default=0)),
                ("credits_consumed", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch_run", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="token_logs", to="question_generation.batchrun",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="token_usage", to="core.user",
                )),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]

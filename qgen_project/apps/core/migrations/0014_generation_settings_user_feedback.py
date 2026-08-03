from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_zero_vector_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationsettings",
            name="user_feedback_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "When on, users must approve or reject each generated question "
                    "before results open."
                ),
            ),
        ),
    ]

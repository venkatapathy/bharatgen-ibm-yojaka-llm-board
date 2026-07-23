from django.db import migrations, models


DEFAULT_COUNCIL_NAMES = (
    "DeepSeek-R1-1.5B",
    "Phi4-mini-3.8B",
)


def seed_default_council_members(apps, schema_editor):
    ModelConfig = apps.get_model("core", "ModelConfig")
    # If nothing is marked yet, enable the small stable pair used in demos.
    if not ModelConfig.objects.filter(is_council_member=True).exists():
        ModelConfig.objects.filter(name__in=DEFAULT_COUNCIL_NAMES).update(is_council_member=True)


def clear_council_members(apps, schema_editor):
    ModelConfig = apps.get_model("core", "ModelConfig")
    ModelConfig.objects.filter(is_council_member=True).update(is_council_member=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_tokenusagelog_metadata_tokenusagelog_model_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="modelconfig",
            name="is_council_member",
            field=models.BooleanField(
                default=False,
                help_text="When Think mode is on, all models marked here are used as the hidden Council of Models (configured in Admin only).",
            ),
        ),
        migrations.RunPython(seed_default_council_members, clear_council_members),
    ]

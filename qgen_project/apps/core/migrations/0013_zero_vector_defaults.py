"""Zero leftover vector pool defaults after the storage merge."""

from django.db import migrations, models


def zero_vector_fields(apps, schema_editor):
    Policy = apps.get_model("core", "OrganizationProvisioningPolicy")
    StorageQuota = apps.get_model("core", "StorageQuota")

    for policy in Policy.objects.all():
        storage = float(policy.storage_pool_gb or 0) + float(
            policy.vector_storage_pool_gb or 0
        )
        default_storage = float(policy.default_storage_limit_gb or 0) + float(
            policy.default_vector_storage_gb or 0
        )
        Policy.objects.filter(pk=policy.pk).update(
            storage_pool_gb=storage,
            default_storage_limit_gb=default_storage,
            vector_storage_pool_gb=0,
            default_vector_storage_gb=0,
        )

    for quota in StorageQuota.objects.all():
        StorageQuota.objects.filter(pk=quota.pk).update(
            max_total_storage_gb=float(quota.max_total_storage_gb or 0)
            + float(quota.max_vector_storage_gb or 0),
            max_vector_storage_gb=0,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_merge_vector_into_storage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="organizationprovisioningpolicy",
            name="vector_storage_pool_gb",
            field=models.FloatField(
                default=0.0,
                help_text="Deprecated: merged into storage_pool_gb. Kept at 0.",
            ),
        ),
        migrations.AlterField(
            model_name="organizationprovisioningpolicy",
            name="default_vector_storage_gb",
            field=models.FloatField(
                default=0.0,
                help_text="Deprecated: merged into default_storage_limit_gb. Kept at 0.",
            ),
        ),
        migrations.AlterField(
            model_name="storagequota",
            name="max_vector_storage_gb",
            field=models.FloatField(default=0.0),
        ),
        migrations.RunPython(zero_vector_fields, migrations.RunPython.noop),
    ]

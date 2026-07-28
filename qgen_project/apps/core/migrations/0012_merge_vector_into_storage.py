"""Merge vector storage pools and user quotas into total storage."""

from django.db import migrations


def merge_vector_into_storage(apps, schema_editor):
    Policy = apps.get_model("core", "OrganizationProvisioningPolicy")
    StorageQuota = apps.get_model("core", "StorageQuota")

    for policy in Policy.objects.all():
        policy.storage_pool_gb = float(policy.storage_pool_gb or 0) + float(
            policy.vector_storage_pool_gb or 0
        )
        policy.default_storage_limit_gb = float(policy.default_storage_limit_gb or 0) + float(
            policy.default_vector_storage_gb or 0
        )
        policy.vector_storage_pool_gb = 0
        policy.default_vector_storage_gb = 0
        policy.save(
            update_fields=[
                "storage_pool_gb",
                "default_storage_limit_gb",
                "vector_storage_pool_gb",
                "default_vector_storage_gb",
            ]
        )

    for quota in StorageQuota.objects.all():
        quota.max_total_storage_gb = float(quota.max_total_storage_gb or 0) + float(
            quota.max_vector_storage_gb or 0
        )
        quota.max_vector_storage_gb = 0
        quota.save(update_fields=["max_total_storage_gb", "max_vector_storage_gb"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_generation_settings"),
    ]

    operations = [
        migrations.RunPython(merge_vector_into_storage, migrations.RunPython.noop),
    ]

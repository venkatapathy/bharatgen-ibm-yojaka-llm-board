from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_org_credit_pool"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationprovisioningpolicy",
            name="storage_pool_gb",
            field=models.FloatField(
                default=50.0,
                help_text="Total file storage (GB) for this organisation. Distributed to users only.",
            ),
        ),
        migrations.AddField(
            model_name="organizationprovisioningpolicy",
            name="vector_storage_pool_gb",
            field=models.FloatField(
                default=20.0,
                help_text="Total vector/embedding storage (GB) for this organisation. Distributed to users only.",
            ),
        ),
        migrations.AlterField(
            model_name="organizationprovisioningpolicy",
            name="credit_pool",
            field=models.IntegerField(
                default=10000,
                help_text="Total monthly credits for this organisation. Distributed to users only.",
            ),
        ),
    ]

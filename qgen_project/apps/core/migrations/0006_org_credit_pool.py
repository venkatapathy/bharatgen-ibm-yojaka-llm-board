from django.db import migrations, models


def copy_pool_from_defaults(apps, schema_editor):
    Policy = apps.get_model("core", "OrganizationProvisioningPolicy")
    for policy in Policy.objects.all():
        # Treat existing default as the org pool so current orgs keep their budget.
        policy.credit_pool = policy.default_monthly_credits or 10_000
        if policy.default_monthly_credits > 10_000:
            # Suggested per-user start should be a slice, not the whole pool.
            policy.default_monthly_credits = min(1_000, policy.credit_pool)
        policy.save(update_fields=["credit_pool", "default_monthly_credits"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_alter_user_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationprovisioningpolicy",
            name="credit_pool",
            field=models.IntegerField(
                default=10000,
                help_text="Total monthly credits for this organisation. Org Admin distributes these among users.",
            ),
        ),
        migrations.AlterField(
            model_name="organizationprovisioningpolicy",
            name="default_monthly_credits",
            field=models.IntegerField(
                default=1000,
                help_text="Suggested starting credits when creating a new user (must fit in remaining pool).",
            ),
        ),
        migrations.RunPython(copy_pool_from_defaults, migrations.RunPython.noop),
    ]

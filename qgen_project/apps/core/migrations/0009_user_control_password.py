from django.db import migrations, models


class Migration(migrations.Migration):
    """Sync ORM with existing core_user.control_password column."""

    dependencies = [
        ("core", "0008_alter_organization_slug"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="control_password",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE core_user "
                        "ALTER COLUMN control_password SET DEFAULT '';"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "UPDATE core_user SET control_password = '' "
                        "WHERE control_password IS NULL;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]

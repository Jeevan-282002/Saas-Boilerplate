from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0002_membership_and_tenant_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="provisioning_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("ready", "Ready")],
                default="ready",
                max_length=16,
            ),
        ),
    ]

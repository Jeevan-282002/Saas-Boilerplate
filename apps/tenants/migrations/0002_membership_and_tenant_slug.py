import django.db.models.deletion
import django.utils.text
from django.conf import settings
from django.db import migrations, models


def populate_tenant_slugs(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    database = schema_editor.connection.alias
    for tenant in Tenant.objects.using(database).filter(slug__isnull=True):
        base = django.utils.text.slugify(tenant.domain)[:54].strip("-") or "organization"
        tenant.slug = f"{base}-{tenant.pk}"
        tenant.save(using=database, update_fields=["slug"])


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="slug",
            field=models.SlugField(
                blank=True,
                db_index=True,
                max_length=63,
                null=True,
            ),
        ),
        migrations.RunPython(populate_tenant_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="tenant",
            name="slug",
            field=models.SlugField(max_length=63, unique=True),
        ),
        migrations.CreateModel(
            name="Membership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("admin", "Admin"), ("member", "Member")],
                        default="member",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="organization_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["tenant__name"]},
        ),
        migrations.AddConstraint(
            model_name="membership",
            constraint=models.UniqueConstraint(
                fields=("user", "tenant"),
                name="unique_user_organization_membership",
            ),
        ),
    ]

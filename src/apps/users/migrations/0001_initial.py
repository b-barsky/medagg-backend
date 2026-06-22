import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_existing_profiles(apps, schema_editor):
    UserProfile = apps.get_model("users", "UserProfile")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)

    UserProfile.objects.bulk_create(
        [UserProfile(user_id=user_id) for user_id in User.objects.values_list(
            "pk",
            flat=True,
        )],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
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
                    "organization",
                    models.CharField(blank=True, max_length=255),
                ),
                (
                    "job_title",
                    models.CharField(blank=True, max_length=255),
                ),
                (
                    "location",
                    models.CharField(blank=True, max_length=255),
                ),
                (
                    "website",
                    models.URLField(blank=True, max_length=500),
                ),
                (
                    "bio",
                    models.TextField(blank=True, max_length=1000),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("user_id",)},
        ),
        migrations.RunPython(
            create_existing_profiles,
            migrations.RunPython.noop,
        ),
    ]

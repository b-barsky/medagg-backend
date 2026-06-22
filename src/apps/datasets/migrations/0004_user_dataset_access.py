from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_import_requesters_and_memberships(apps, schema_editor):
    DatasetImport = apps.get_model("datasets", "DatasetImport")
    DatasetImportRequester = apps.get_model(
        "datasets",
        "DatasetImportRequester",
    )
    DatasetMembership = apps.get_model(
        "datasets",
        "DatasetMembership",
    )

    imports = DatasetImport.objects.exclude(requested_by_id=None).iterator()

    for import_run in imports:
        DatasetImportRequester.objects.get_or_create(
            import_run_id=import_run.pk,
            user_id=import_run.requested_by_id,
            defaults={
                "accepted_license": import_run.accepted_license,
                "license_fingerprint": import_run.license_fingerprint,
                "access_granted_at": (
                    import_run.finished_at
                    if import_run.status == "succeeded"
                    else None
                ),
            },
        )

        if (
            import_run.status == "succeeded"
            and import_run.dataset_id is not None
        ):
            DatasetMembership.objects.get_or_create(
                user_id=import_run.requested_by_id,
                dataset_id=import_run.dataset_id,
                defaults={
                    "first_import_id": import_run.pk,
                    "acquisition": "imported",
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0001_initial"),
        ("datasets", "0003_artifact_ingestion"),
    ]

    operations = [
        migrations.CreateModel(
            name="DatasetMembership",
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
                    "acquisition",
                    models.CharField(
                        choices=[
                            ("imported", "Imported"),
                            ("shared", "Shared"),
                            ("derived", "Derived"),
                            ("manual", "Manual"),
                        ],
                        default="imported",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="datasets.dataset",
                    ),
                ),
                (
                    "first_import",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="granted_memberships",
                        to="datasets.datasetimport",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dataset_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["user", "-created_at"],
                        name="datasets_member_user_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "dataset"),
                        name="datasets_member_user_ds_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="DatasetImportRequester",
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
                ("accepted_license", models.BooleanField(default=True)),
                ("license_fingerprint", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "access_granted_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "import_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="requesters",
                        to="datasets.datasetimport",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dataset_import_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("created_at", "id"),
                "indexes": [
                    models.Index(
                        fields=["user", "-created_at"],
                        name="datasets_req_user_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("import_run", "user"),
                        name="datasets_req_import_user_uniq",
                    )
                ],
            },
        ),
        migrations.RunPython(
            backfill_import_requesters_and_memberships,
            migrations.RunPython.noop,
        ),
    ]

# Generated for the artifact-ingestion stage.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def migrate_legacy_dataset_fields(apps, schema_editor):
    Dataset = apps.get_model("datasets", "Dataset")

    for dataset in Dataset.objects.all().iterator():
        dataset.description = dataset.description or ""
        dataset.source_url = dataset.external_path or ""
        dataset.license_names = (
            [dataset.license]
            if dataset.license
            else []
        )
        dataset.size_bytes = (
            dataset.size * 1024 * 1024
            if dataset.size is not None
            else None
        )
        dataset.legacy_metadata = {
            "external_path": dataset.external_path,
            "local_path": dataset.local_path,
            "size_megabytes": dataset.size,
        }
        dataset.save(
            update_fields=(
                "description",
                "source_url",
                "license_names",
                "legacy_metadata",
                "size_bytes",
            )
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0002_seed_kaggle_source"),
        ("datasets", "0002_dataset_license"),
    ]

    operations = [
        migrations.AddField(
            model_name="dataset",
            name="origin",
            field=models.CharField(
                choices=[
                    ("imported", "Imported"),
                    ("derived", "Derived"),
                    ("manual", "Manual"),
                ],
                default="manual",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="dataset",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("internal", "Internal"),
                    ("private", "Private"),
                ],
                default="internal",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="dataset",
            name="source_dataset",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="local_dataset",
                to="catalog.sourcedataset",
            ),
        ),
        migrations.AddField(
            model_name="dataset",
            name="source_url",
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name="dataset",
            name="license_names",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="dataset",
            name="size_bytes",
            field=models.PositiveBigIntegerField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="dataset",
            name="legacy_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(
            migrate_legacy_dataset_fields,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="dataset",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="dataset",
            name="record_count",
            field=models.PositiveBigIntegerField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="dataset",
            name="anatomical_area",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="datasets.anatomicalarea",
            ),
        ),
        migrations.AlterField(
            model_name="dataset",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name="dataset",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.RemoveField(
            model_name="dataset",
            name="external_path",
        ),
        migrations.RemoveField(
            model_name="dataset",
            name="local_path",
        ),
        migrations.RemoveField(
            model_name="dataset",
            name="size",
        ),
        migrations.AlterModelOptions(
            name="dataset",
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.AlterUniqueTogether(
            name="datasetmodality",
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name="datasetmltask",
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name="datasettag",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="datasetmodality",
            constraint=models.UniqueConstraint(
                fields=("dataset", "modality"),
                name="datasets_dataset_modality_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="datasetmltask",
            constraint=models.UniqueConstraint(
                fields=("dataset", "ml_task"),
                name="datasets_dataset_ml_task_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="datasettag",
            constraint=models.UniqueConstraint(
                fields=("dataset", "tag"),
                name="datasets_dataset_tag_uniq",
            ),
        ),
        migrations.CreateModel(
            name="DatasetVersion",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("number", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("staging", "Staging"),
                            ("available", "Available"),
                            ("failed", "Failed"),
                        ],
                        default="staging",
                        max_length=16,
                    ),
                ),
                (
                    "source_revision",
                    models.CharField(blank=True, max_length=64),
                ),
                (
                    "source_version",
                    models.CharField(blank=True, max_length=100),
                ),
                (
                    "source_updated_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "checksum_sha256",
                    models.CharField(blank=True, max_length=64),
                ),
                (
                    "size_bytes",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "record_count",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "manifest",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, max_length=64),
                ),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "available_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="versions",
                        to="datasets.dataset",
                    ),
                ),
            ],
            options={
                "ordering": ("-number",),
                "indexes": [
                    models.Index(
                        fields=["dataset", "status", "-number"],
                        name="datasets_ver_status_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("dataset", "number"),
                        name="datasets_version_number_uniq",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("source_revision", ""),
                            _negated=True,
                        ),
                        fields=("dataset", "source_revision"),
                        name="datasets_version_revision_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="DatasetArtifact",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("source_archive", "Source archive"),
                            ("data", "Data"),
                            ("manifest", "Manifest"),
                        ],
                        default="source_archive",
                        max_length=32,
                    ),
                ),
                (
                    "storage_backend",
                    models.CharField(default="s3", max_length=32),
                ),
                ("bucket", models.CharField(max_length=255)),
                ("object_key", models.CharField(max_length=1024)),
                (
                    "object_version_id",
                    models.CharField(blank=True, max_length=255),
                ),
                ("filename", models.CharField(max_length=500)),
                (
                    "content_type",
                    models.CharField(
                        default="application/octet-stream",
                        max_length=255,
                    ),
                ),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("checksum_sha256", models.CharField(max_length=64)),
                ("etag", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "dataset_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="artifacts",
                        to="datasets.datasetversion",
                    ),
                ),
            ],
            options={
                "ordering": ("kind", "filename"),
                "indexes": [
                    models.Index(
                        fields=["checksum_sha256"],
                        name="datasets_artifact_sha_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "dataset_version",
                            "kind",
                            "object_key",
                        ),
                        name="datasets_artifact_object_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="DatasetImport",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("retrying", "Retrying"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("rejected", "Rejected"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                (
                    "celery_task_id",
                    models.CharField(blank=True, max_length=255),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("source_revision", models.CharField(max_length=64)),
                (
                    "source_version",
                    models.CharField(blank=True, max_length=100),
                ),
                (
                    "source_updated_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("accepted_license", models.BooleanField(default=False)),
                (
                    "license_names_snapshot",
                    models.JSONField(blank=True, default=list),
                ),
                ("license_fingerprint", models.CharField(max_length=64)),
                (
                    "policy_snapshot",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, max_length=64),
                ),
                ("error_message", models.TextField(blank=True)),
                (
                    "queued_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "started_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "last_attempt_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="import_runs",
                        to="datasets.dataset",
                    ),
                ),
                (
                    "dataset_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="import_runs",
                        to="datasets.datasetversion",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dataset_imports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="import_runs",
                        to="catalog.sourcedataset",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["status", "-created_at"],
                        name="datasets_import_status_idx",
                    ),
                    models.Index(
                        fields=["source_dataset", "source_revision"],
                        name="datasets_import_source_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                ("queued", "running", "retrying"),
                            )
                        ),
                        fields=("source_dataset", "source_revision"),
                        name="datasets_active_import_uniq",
                    )
                ],
            },
        ),
    ]

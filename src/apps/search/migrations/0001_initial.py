# Generated for Django 5.2.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("catalog", "0002_seed_kaggle_source"),
    ]

    operations = [
        migrations.CreateModel(
            name="SearchRun",
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
                    "query",
                    models.CharField(max_length=100),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("partial", "Partial"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                (
                    "deadline_at",
                    models.DateTimeField(),
                ),
                (
                    "started_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "finished_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["status", "deadline_at"],
                        name="search_run_state_deadline_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SearchProviderRun",
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
                    "position",
                    models.PositiveSmallIntegerField(),
                ),
                (
                    "provider_page",
                    models.PositiveIntegerField(default=1),
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
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                (
                    "task_id",
                    models.UUIDField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "attempt_count",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "result_count",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "error_code",
                    models.CharField(
                        blank=True,
                        max_length=64,
                    ),
                ),
                (
                    "error_message",
                    models.TextField(blank=True),
                ),
                (
                    "started_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "last_attempt_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "finished_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "search_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="provider_runs",
                        to="search.searchrun",
                    ),
                ),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="search_provider_runs",
                        to="catalog.datasource",
                    ),
                ),
            ],
            options={
                "ordering": ("position", "id"),
                "indexes": [
                    models.Index(
                        fields=["status"],
                        name="search_provider_state_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("search_run", "source"),
                        name="search_pr_run_source_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("search_run", "position"),
                        name="search_pr_run_position_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SearchResult",
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
                    "rank",
                    models.PositiveIntegerField(),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "provider_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_results",
                        to="search.searchproviderrun",
                    ),
                ),
                (
                    "search_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_results",
                        to="search.searchrun",
                    ),
                ),
                (
                    "source_dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_results",
                        to="catalog.sourcedataset",
                    ),
                ),
            ],
            options={
                "ordering": (
                    "provider_run__position",
                    "rank",
                    "id",
                ),
                "indexes": [
                    models.Index(
                        fields=["search_run", "rank"],
                        name="search_result_order_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("search_run", "source_dataset"),
                        name="search_result_run_dataset_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("provider_run", "rank"),
                        name="search_result_provider_rank_uniq",
                    ),
                ],
            },
        ),
    ]

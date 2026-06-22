from django.db import migrations, models
from django.utils import timezone


def initialize_existing_search_results(apps, schema_editor):
    SearchProviderRun = apps.get_model(
        "search",
        "SearchProviderRun",
    )
    SearchResult = apps.get_model(
        "search",
        "SearchResult",
    )
    SearchRun = apps.get_model(
        "search",
        "SearchRun",
    )

    now = timezone.now()
    provider_ids: set[int] = set()

    for result in SearchResult.objects.select_related(
        "source_dataset"
    ).iterator():
        provider_ids.add(result.provider_run_id)

        if result.source_dataset.detail_status == "complete":
            result.enrichment_status = "succeeded"
            result.enrichment_error_code = ""
            result.enrichment_error_message = ""
        else:
            result.enrichment_status = "failed"
            result.enrichment_error_code = (
                "legacy_result_not_enriched"
            )
            result.enrichment_error_message = (
                "This result existed before durable detail tasks were "
                "introduced. Run a new search to enrich it."
            )

        result.enrichment_finished_at = now
        result.save(
            update_fields=(
                "enrichment_status",
                "enrichment_error_code",
                "enrichment_error_message",
                "enrichment_finished_at",
            )
        )

    search_run_ids: set[object] = set()

    for provider_run in SearchProviderRun.objects.filter(
        pk__in=provider_ids
    ).iterator():
        statuses = list(
            SearchResult.objects
            .filter(provider_run_id=provider_run.pk)
            .values_list("enrichment_status", flat=True)
        )
        completed_count = statuses.count("succeeded")
        failed_count = statuses.count("failed")

        provider_run.result_count = len(statuses)
        provider_run.detail_completed_count = completed_count
        provider_run.detail_failed_count = failed_count
        provider_run.status = (
            "succeeded"
            if failed_count == 0
            else "partial"
        )
        provider_run.finished_at = (
            provider_run.finished_at or now
        )
        provider_run.save(
            update_fields=(
                "result_count",
                "detail_completed_count",
                "detail_failed_count",
                "status",
                "finished_at",
            )
        )
        search_run_ids.add(provider_run.search_run_id)

    for search_run in SearchRun.objects.filter(
        pk__in=search_run_ids
    ).iterator():
        statuses = list(
            SearchProviderRun.objects
            .filter(search_run_id=search_run.pk)
            .values_list("status", flat=True)
        )

        if statuses and all(
            status == "succeeded"
            for status in statuses
        ):
            search_run.status = "completed"
        elif any(
            status in {"succeeded", "partial"}
            for status in statuses
        ):
            search_run.status = "partial"
        elif statuses and all(
            status == "failed"
            for status in statuses
        ):
            search_run.status = "failed"
        else:
            continue

        search_run.finished_at = search_run.finished_at or now
        search_run.save(
            update_fields=("status", "finished_at")
        )


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchproviderrun",
            name="detail_completed_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="searchproviderrun",
            name="detail_failed_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="searchproviderrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("retrying", "Retrying"),
                    ("enriching", "Enriching"),
                    ("succeeded", "Succeeded"),
                    ("partial", "Partial"),
                    ("failed", "Failed"),
                ],
                default="queued",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_attempt_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_error_code",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_error_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_status",
            field=models.CharField(
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
        migrations.AddField(
            model_name="searchresult",
            name="enrichment_task_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="searchresult",
            index=models.Index(
                fields=["enrichment_status"],
                name="search_result_enrich_idx",
            ),
        ),
        migrations.RunPython(
            initialize_existing_search_results,
            migrations.RunPython.noop,
        ),
    ]

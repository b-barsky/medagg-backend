from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.search.models import (
    SearchProviderStatus,
    SearchResultEnrichmentStatus,
    SearchRunStatus,
)
from apps.search.services import SearchRunService


class SearchEnrichmentServiceTests(TestCase):
    def setUp(self):
        self.kaggle = DataSource.objects.get(slug="kaggle")
        self.kaggle.is_enabled = True
        self.kaggle.save(
            update_fields=("is_enabled", "updated_at")
        )
        self.service = SearchRunService()
        self.search_run = self.service.create_run(
            "lung cancer",
            source_slugs=("kaggle",),
        )
        self.provider_run = self.search_run.provider_runs.get()
        self.service.start_provider(
            self.provider_run.pk,
            task_id=str(self.provider_run.task_id),
        )

    def create_dataset(
        self,
        external_id: str,
        *,
        detail_status: str = MetadataStatus.PENDING,
    ) -> SourceDataset:
        return SourceDataset.objects.create(
            source=self.kaggle,
            external_id=external_id,
            source_url=(
                "https://www.kaggle.com/datasets/"
                f"{external_id}"
            ),
            title=external_id,
            detail_status=detail_status,
        )

    def test_publish_makes_summaries_visible_while_enriching(self):
        dataset = self.create_dataset("owner/lungs")

        published = self.service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )

        self.assertTrue(published)
        result = self.provider_run.search_results.get()
        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.QUEUED,
        )
        self.assertIsNotNone(result.enrichment_task_id)
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.ENRICHING,
        )
        self.assertEqual(self.provider_run.result_count, 1)
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.RUNNING,
        )
        self.assertEqual(
            self.service.results_queryset(
                self.search_run.pk
            ).count(),
            1,
        )

    @patch(
        "apps.search.tasks.enrich_search_result.apply_async"
    )
    def test_detail_dispatch_uses_dedicated_queue(
        self,
        apply_async,
    ):
        dataset = self.create_dataset("owner/lungs")
        self.service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )
        result = self.provider_run.search_results.get()

        dispatch = self.service.enqueue_result_enrichments(
            self.provider_run.pk
        )

        self.assertEqual(dispatch.queued_count, 1)
        self.assertEqual(dispatch.publish_failed_count, 0)
        apply_async.assert_called_once_with(
            args=(result.pk,),
            task_id=str(result.enrichment_task_id),
            queue=settings.SEARCH_DETAIL_TASK_QUEUE,
        )

    @patch(
        "apps.search.tasks.enrich_search_result.apply_async",
        side_effect=OperationalError("redis unavailable"),
    )
    def test_detail_publish_failure_returns_partial_summaries(
        self,
        apply_async,
    ):
        dataset = self.create_dataset("owner/lungs")
        self.service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )

        dispatch = self.service.enqueue_result_enrichments(
            self.provider_run.pk
        )

        self.assertEqual(dispatch.queued_count, 0)
        self.assertEqual(dispatch.publish_failed_count, 1)
        result = self.provider_run.search_results.get()
        result.refresh_from_db()
        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.FAILED,
        )
        self.assertEqual(
            result.enrichment_error_code,
            "broker_publish_failed",
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.PARTIAL,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.PARTIAL,
        )
        apply_async.assert_called_once()

    def test_repeated_summary_publish_preserves_detail_task_identity(self):
        dataset = self.create_dataset("owner/lungs")
        self.service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )
        first = self.provider_run.search_results.get()
        first_id = first.pk
        first_task_id = first.enrichment_task_id

        self.service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )

        second = self.provider_run.search_results.get()
        self.assertEqual(second.pk, first_id)
        self.assertEqual(
            second.enrichment_task_id,
            first_task_id,
        )
        self.assertEqual(
            second.enrichment_status,
            SearchResultEnrichmentStatus.QUEUED,
        )

    def test_mixed_detail_outcomes_produce_exact_progress(self):
        first = self.create_dataset("owner/first")
        second = self.create_dataset("owner/second")
        self.service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(first.pk, second.pk),
        )
        results = list(
            self.provider_run.search_results.order_by("rank")
        )

        self.service.start_result_enrichment(
            results[0].pk,
            task_id=str(results[0].enrichment_task_id),
        )
        first.detail_status = MetadataStatus.COMPLETE
        first.detail_fetched_at = timezone.now()
        first.save(
            update_fields=(
                "detail_status",
                "detail_fetched_at",
                "updated_at",
            )
        )
        self.service.complete_result_enrichment(results[0].pk)

        self.service.start_result_enrichment(
            results[1].pk,
            task_id=str(results[1].enrichment_task_id),
        )
        self.service.fail_result_enrichment(
            results[1].pk,
            error_code="provider_error",
            error_message="Metadata unavailable",
        )

        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(self.provider_run.result_count, 2)
        self.assertEqual(
            self.provider_run.detail_completed_count,
            1,
        )
        self.assertEqual(
            self.provider_run.detail_failed_count,
            1,
        )
        self.assertEqual(
            self.provider_run.detail_pending_count,
            0,
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.PARTIAL,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.PARTIAL,
        )


class StalledProviderDetectionTests(TestCase):
    @override_settings(
        SEARCH_PROVIDER_START_TIMEOUT_SECONDS=5
    )
    def test_polling_marks_an_unconsumed_provider_task_failed(self):
        kaggle = DataSource.objects.get(slug="kaggle")
        kaggle.is_enabled = True
        kaggle.save(
            update_fields=("is_enabled", "updated_at")
        )
        service = SearchRunService()
        search_run = service.create_run(
            "lung cancer",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        provider_run.created_at = (
            timezone.now() - timedelta(seconds=10)
        )
        provider_run.save(
            update_fields=("created_at", "updated_at")
        )

        changed = service.expire_run_if_needed(search_run.pk)

        self.assertTrue(changed)
        provider_run.refresh_from_db()
        search_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.FAILED,
        )
        self.assertEqual(
            provider_run.error_code,
            "worker_not_started",
        )
        self.assertEqual(
            search_run.status,
            SearchRunStatus.FAILED,
        )

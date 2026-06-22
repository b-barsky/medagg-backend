from unittest.mock import Mock, patch
from uuid import uuid4

from celery.exceptions import Retry, SoftTimeLimitExceeded
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.catalog.providers.exceptions import (
    ProviderResponseError,
    ProviderUnavailableError,
)
from apps.catalog.services import CatalogSyncResult
from apps.search.models import (
    SearchProviderStatus,
    SearchResultEnrichmentStatus,
    SearchRunStatus,
)
from apps.search.services import (
    EnrichmentDispatchResult,
    SearchRunService,
)
from apps.search.tasks import (
    enrich_search_result,
    expire_search_run,
    search_provider,
)


class SearchTaskTestCase(TestCase):
    def setUp(self):
        self.kaggle = DataSource.objects.get(slug="kaggle")
        self.kaggle.is_enabled = True
        self.kaggle.save(
            update_fields=("is_enabled", "updated_at")
        )
        self.search_service = SearchRunService()
        self.search_run = self.search_service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        self.provider_run = self.search_run.provider_runs.get()

    def create_dataset(
        self,
        *,
        external_id: str = "owner/lungs",
        detail_status: str = MetadataStatus.PENDING,
    ) -> SourceDataset:
        return SourceDataset.objects.create(
            source=self.kaggle,
            external_id=external_id,
            source_url=(
                "https://www.kaggle.com/datasets/"
                f"{external_id}"
            ),
            title="Lung Data",
            detail_status=detail_status,
        )

    @staticmethod
    def sync_result(
        *record_ids: int,
    ) -> CatalogSyncResult:
        return CatalogSyncResult(
            source_slug="kaggle",
            query="lungs",
            page=1,
            fetched_count=len(record_ids),
            stored_count=len(record_ids),
            created_count=len(record_ids),
            refreshed_count=0,
            record_ids=tuple(record_ids),
        )

    def prepare_result(
        self,
        *,
        detail_status: str = MetadataStatus.PENDING,
    ):
        dataset = self.create_dataset(
            detail_status=detail_status
        )
        self.search_service.start_provider(
            self.provider_run.pk,
            task_id=str(self.provider_run.task_id),
        )
        self.search_service.publish_provider_results(
            self.provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )
        result = self.provider_run.search_results.get()
        return dataset, result


class SearchProviderTaskTests(SearchTaskTestCase):
    def execute(self, catalog_service):
        dispatch = EnrichmentDispatchResult(
            queued_count=1,
            publish_failed_count=0,
        )

        with (
            patch(
                "apps.search.tasks.CatalogService",
                return_value=catalog_service,
            ),
            patch.object(
                SearchRunService,
                "enqueue_result_enrichments",
                return_value=dispatch,
            ) as enqueue_details,
        ):
            task_result = search_provider.apply(
                args=(self.provider_run.pk,),
                task_id=str(self.provider_run.task_id),
                throw=True,
            )

        return task_result, enqueue_details

    def test_summary_results_are_visible_before_detail_enrichment(self):
        dataset = self.create_dataset()
        catalog_service = Mock()
        catalog_service.search_and_upsert.return_value = (
            self.sync_result(dataset.pk)
        )

        _, enqueue_details = self.execute(catalog_service)

        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        result = self.provider_run.search_results.get()

        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.ENRICHING,
        )
        self.assertEqual(self.provider_run.result_count, 1)
        self.assertEqual(
            self.provider_run.detail_completed_count,
            0,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.RUNNING,
        )
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.QUEUED,
        )
        catalog_service.search_and_upsert.assert_called_once_with(
            source_slug="kaggle",
            query="lungs",
            page=1,
        )
        enqueue_details.assert_called_once_with(
            self.provider_run.pk
        )

    def test_already_enriched_result_completes_provider(self):
        dataset = self.create_dataset(
            detail_status=MetadataStatus.COMPLETE
        )
        catalog_service = Mock()
        catalog_service.search_and_upsert.return_value = (
            self.sync_result(dataset.pk)
        )

        self.execute(catalog_service)

        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        result = self.provider_run.search_results.get()
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.SUCCEEDED,
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.SUCCEEDED,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.COMPLETED,
        )

    @patch(
        "apps.search.tasks._provider_retry_delay",
        return_value=5,
    )
    def test_temporary_provider_failure_schedules_retry(
        self,
        retry_delay,
    ):
        catalog_service = Mock()
        catalog_service.search_and_upsert.side_effect = (
            ProviderUnavailableError("temporary outage")
        )

        with self.assertRaises(Retry):
            self.execute(catalog_service)

        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.RETRYING,
        )
        self.assertEqual(
            self.provider_run.error_code,
            "provider_unavailable",
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.RUNNING,
        )
        retry_delay.assert_called_once_with(0)

    def test_permanent_provider_failure_is_persisted(self):
        catalog_service = Mock()
        catalog_service.search_and_upsert.side_effect = (
            ProviderResponseError("invalid response")
        )

        self.execute(catalog_service)

        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.FAILED,
        )
        self.assertEqual(
            self.provider_run.error_code,
            "ProviderResponseError",
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.FAILED,
        )

    @patch(
        "apps.search.tasks._provider_retry_delay",
        return_value=5,
    )
    def test_soft_timeout_is_retried(
        self,
        retry_delay,
    ):
        catalog_service = Mock()
        catalog_service.search_and_upsert.side_effect = (
            SoftTimeLimitExceeded()
        )

        with self.assertRaises(Retry):
            self.execute(catalog_service)

        self.provider_run.refresh_from_db()
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.RETRYING,
        )
        self.assertEqual(
            self.provider_run.error_code,
            "provider_soft_timeout",
        )
        retry_delay.assert_called_once_with(0)

    def test_stale_broker_message_is_ignored(self):
        catalog_service = Mock()

        with (
            patch(
                "apps.search.tasks.CatalogService",
                return_value=catalog_service,
            ),
            patch.object(
                SearchRunService,
                "enqueue_result_enrichments",
            ) as enqueue_details,
        ):
            search_provider.apply(
                args=(self.provider_run.pk,),
                task_id=str(uuid4()),
                throw=True,
            )

        catalog_service.search_and_upsert.assert_not_called()
        enqueue_details.assert_not_called()
        self.provider_run.refresh_from_db()
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.QUEUED,
        )


class SearchResultEnrichmentTaskTests(SearchTaskTestCase):
    def execute(self, result, catalog_service):
        with patch(
            "apps.search.tasks.CatalogService",
            return_value=catalog_service,
        ):
            return enrich_search_result.apply(
                args=(result.pk,),
                task_id=str(result.enrichment_task_id),
                throw=True,
            )

    def test_successful_detail_task_completes_run(self):
        dataset, result = self.prepare_result()
        catalog_service = Mock()

        def fetch_and_enrich(source_dataset_id):
            self.assertEqual(source_dataset_id, dataset.pk)
            dataset.detail_status = MetadataStatus.COMPLETE
            dataset.description = "Complete Kaggle metadata"
            dataset.detail_metadata = {
                "info": {
                    "description": dataset.description,
                }
            }
            dataset.detail_fetched_at = timezone.now()
            dataset.save(
                update_fields=(
                    "detail_status",
                    "description",
                    "detail_metadata",
                    "detail_fetched_at",
                    "updated_at",
                )
            )
            return dataset

        catalog_service.fetch_and_enrich.side_effect = (
            fetch_and_enrich
        )

        self.execute(result, catalog_service)

        result.refresh_from_db()
        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        dataset.refresh_from_db()

        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.SUCCEEDED,
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.SUCCEEDED,
        )
        self.assertEqual(
            self.provider_run.detail_completed_count,
            1,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.COMPLETED,
        )
        self.assertEqual(
            dataset.description,
            "Complete Kaggle metadata",
        )

    @patch(
        "apps.search.tasks._detail_retry_delay",
        return_value=7,
    )
    def test_temporary_detail_failure_schedules_retry(
        self,
        retry_delay,
    ):
        _, result = self.prepare_result()
        catalog_service = Mock()
        catalog_service.fetch_and_enrich.side_effect = (
            ProviderUnavailableError("temporary outage")
        )

        with self.assertRaises(Retry):
            self.execute(result, catalog_service)

        result.refresh_from_db()
        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.RETRYING,
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.ENRICHING,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.RUNNING,
        )
        retry_delay.assert_called_once_with(0)

    def test_permanent_detail_failure_yields_partial_run(self):
        _, result = self.prepare_result()
        catalog_service = Mock()
        catalog_service.fetch_and_enrich.side_effect = (
            ProviderResponseError("invalid metadata")
        )

        self.execute(result, catalog_service)

        result.refresh_from_db()
        self.provider_run.refresh_from_db()
        self.search_run.refresh_from_db()
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.FAILED,
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.PARTIAL,
        )
        self.assertEqual(
            self.provider_run.detail_failed_count,
            1,
        )
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.PARTIAL,
        )

    def test_stale_detail_task_id_is_ignored(self):
        _, result = self.prepare_result()
        catalog_service = Mock()

        with patch(
            "apps.search.tasks.CatalogService",
            return_value=catalog_service,
        ):
            enrich_search_result.apply(
                args=(result.pk,),
                task_id=str(uuid4()),
                throw=True,
            )

        catalog_service.fetch_and_enrich.assert_not_called()
        result.refresh_from_db()
        self.assertEqual(
            result.enrichment_status,
            SearchResultEnrichmentStatus.QUEUED,
        )


class SearchRunDeadlineTaskTests(SearchTaskTestCase):
    def test_deadline_task_expires_overdue_run(self):
        self.search_run.deadline_at = timezone.now()
        self.search_run.save(
            update_fields=("deadline_at", "updated_at")
        )

        expire_search_run.apply(
            args=(str(self.search_run.pk),),
            throw=True,
        )

        self.search_run.refresh_from_db()
        self.provider_run.refresh_from_db()
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.FAILED,
        )
        self.assertEqual(
            self.provider_run.status,
            SearchProviderStatus.FAILED,
        )
        self.assertEqual(
            self.provider_run.error_code,
            "search_deadline_exceeded",
        )

    def test_deadline_task_is_noop_before_deadline(self):
        expire_search_run.apply(
            args=(str(self.search_run.pk),),
            throw=True,
        )

        self.search_run.refresh_from_db()
        self.assertEqual(
            self.search_run.status,
            SearchRunStatus.QUEUED,
        )

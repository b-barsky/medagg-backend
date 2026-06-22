from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from kombu.exceptions import OperationalError

from apps.catalog.models import DataSource, SourceDataset
from apps.catalog.providers.base import DatasetProvider
from apps.catalog.providers.dto import (
    ProviderDatasetDetails,
    ProviderSearchPage,
)
from apps.catalog.providers.registry import ProviderRegistry
from apps.search.models import (
    SearchProviderStatus,
    SearchResult,
    SearchRunStatus,
)
from apps.search.services import (
    SearchResultPersistenceError,
    SearchRunService,
    SearchSourceValidationError,
)


class EmptyProvider(DatasetProvider):
    slug = "kaggle"

    def search_summary(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> ProviderSearchPage:
        return ProviderSearchPage(
            query=query,
            page=page,
            items=(),
        )

    def fetch_details(
        self,
        external_id: str,
    ) -> ProviderDatasetDetails:
        return ProviderDatasetDetails(
            external_id=external_id,
        )


class MosMedProvider(EmptyProvider):
    slug = "mosmed"


class SearchRunServiceTests(TestCase):
    def setUp(self):
        self.kaggle = DataSource.objects.get(
            slug="kaggle"
        )
        self.kaggle.is_enabled = True
        self.kaggle.save(
            update_fields=("is_enabled", "updated_at")
        )
        self.mosmed = DataSource.objects.create(
            slug="mosmed",
            name="MosMed",
            base_url="https://mosmed.ai",
        )

        self.registry = ProviderRegistry()
        self.registry.register("kaggle", EmptyProvider)
        self.registry.register("mosmed", MosMedProvider)
        self.service = SearchRunService(self.registry)

    @staticmethod
    def create_dataset(
        source: DataSource,
        external_id: str,
        title: str,
    ) -> SourceDataset:
        return SourceDataset.objects.create(
            source=source,
            external_id=external_id,
            source_url=(
                f"{source.base_url.rstrip('/')}/datasets/"
                f"{external_id}"
            ),
            title=title,
        )

    def test_create_run_builds_ordered_provider_rows(self):
        search_run = self.service.create_run(
            " lung cancer ",
            source_slugs=("mosmed", "kaggle"),
            provider_page=2,
        )

        self.assertEqual(search_run.query, "lung cancer")
        self.assertEqual(
            search_run.status,
            SearchRunStatus.QUEUED,
        )
        self.assertGreater(
            search_run.deadline_at,
            search_run.created_at,
        )

        providers = list(search_run.provider_runs.all())
        self.assertEqual(
            [provider.source.slug for provider in providers],
            ["mosmed", "kaggle"],
        )
        self.assertEqual(
            [provider.position for provider in providers],
            [1, 2],
        )
        self.assertTrue(
            all(provider.task_id for provider in providers)
        )
        self.assertTrue(
            all(provider.provider_page == 2 for provider in providers)
        )

    def test_omitted_sources_selects_all_enabled_registered_sources(self):
        search_run = self.service.create_run("lungs")

        self.assertEqual(
            {
                provider.source.slug
                for provider in search_run.provider_runs.all()
            },
            {"kaggle", "mosmed"},
        )

    def test_explicit_empty_source_list_is_rejected(self):
        with self.assertRaises(
            SearchSourceValidationError
        ):
            self.service.create_run(
                "lungs",
                source_slugs=(),
            )

    def test_unknown_or_disabled_source_is_rejected(self):
        self.mosmed.is_enabled = False
        self.mosmed.save(
            update_fields=("is_enabled", "updated_at")
        )

        with self.assertRaises(
            SearchSourceValidationError
        ) as context:
            self.service.create_run(
                "lungs",
                source_slugs=("mosmed", "unknown"),
            )

        self.assertEqual(
            context.exception.unavailable_sources,
            ("mosmed", "unknown"),
        )

    def test_start_provider_sets_running_state(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()

        execution = self.service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )

        self.assertIsNotNone(execution)
        provider_run.refresh_from_db()
        search_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.RUNNING,
        )
        self.assertEqual(provider_run.attempt_count, 1)
        self.assertIsNotNone(provider_run.started_at)
        self.assertEqual(
            search_run.status,
            SearchRunStatus.RUNNING,
        )

    def test_stale_task_id_is_ignored(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()

        execution = self.service.start_provider(
            provider_run.pk,
            task_id=str(uuid4()),
        )

        self.assertIsNone(execution)
        provider_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.QUEUED,
        )
        self.assertEqual(provider_run.attempt_count, 0)

    def test_complete_provider_persists_ranked_results(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        first = self.create_dataset(
            self.kaggle,
            "owner/first",
            "First",
        )
        second = self.create_dataset(
            self.kaggle,
            "owner/second",
            "Second",
        )
        self.service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )

        completed = self.service.complete_provider(
            provider_run.pk,
            source_dataset_ids=(second.pk, first.pk, second.pk),
        )

        self.assertTrue(completed)
        provider_run.refresh_from_db()
        search_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.SUCCEEDED,
        )
        self.assertEqual(provider_run.result_count, 2)
        self.assertEqual(
            search_run.status,
            SearchRunStatus.COMPLETED,
        )
        self.assertEqual(
            list(
                SearchResult.objects.values_list(
                    "source_dataset_id",
                    "rank",
                )
            ),
            [
                (second.pk, 1),
                (first.pk, 2),
            ],
        )

    def test_one_success_and_one_failure_yields_partial_run(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle", "mosmed"),
        )
        provider_runs = {
            provider.source.slug: provider
            for provider in search_run.provider_runs.all()
        }
        dataset = self.create_dataset(
            self.kaggle,
            "owner/lungs",
            "Lungs",
        )

        self.service.start_provider(
            provider_runs["kaggle"].pk,
            task_id=str(provider_runs["kaggle"].task_id),
        )
        self.service.complete_provider(
            provider_runs["kaggle"].pk,
            source_dataset_ids=(dataset.pk,),
        )
        self.service.fail_provider(
            provider_runs["mosmed"].pk,
            error_code="unavailable",
            error_message="MosMed is unavailable",
        )

        search_run.refresh_from_db()
        self.assertEqual(
            search_run.status,
            SearchRunStatus.PARTIAL,
        )
        self.assertIsNotNone(search_run.finished_at)

    def test_all_provider_failures_yield_failed_run(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle", "mosmed"),
        )

        for provider_run in search_run.provider_runs.all():
            self.service.fail_provider(
                provider_run.pk,
                error_code="unavailable",
                error_message="Provider failed",
            )

        search_run.refresh_from_db()
        self.assertEqual(
            search_run.status,
            SearchRunStatus.FAILED,
        )

    def test_retrying_provider_keeps_run_running(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        self.service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )

        scheduled = self.service.mark_provider_retrying(
            provider_run.pk,
            error_code="provider_unavailable",
            error_message="Temporary outage",
        )

        self.assertTrue(scheduled)
        provider_run.refresh_from_db()
        search_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.RETRYING,
        )
        self.assertEqual(
            search_run.status,
            SearchRunStatus.RUNNING,
        )

    def test_expire_run_fails_active_providers(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle", "mosmed"),
        )
        search_run.deadline_at = (
            timezone.now() - timedelta(seconds=1)
        )
        search_run.save(
            update_fields=("deadline_at", "updated_at")
        )

        expired = self.service.expire_run_if_needed(
            search_run.pk
        )

        self.assertTrue(expired)
        search_run.refresh_from_db()
        self.assertEqual(
            search_run.status,
            SearchRunStatus.FAILED,
        )
        self.assertEqual(
            set(
                search_run.provider_runs.values_list(
                    "status",
                    flat=True,
                )
            ),
            {SearchProviderStatus.FAILED},
        )

    def test_late_provider_results_are_discarded(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        dataset = self.create_dataset(
            self.kaggle,
            "owner/lungs",
            "Lungs",
        )
        self.service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )
        search_run.deadline_at = (
            timezone.now() - timedelta(seconds=1)
        )
        search_run.save(
            update_fields=("deadline_at", "updated_at")
        )

        completed = self.service.complete_provider(
            provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )

        self.assertFalse(completed)
        self.assertFalse(SearchResult.objects.exists())
        provider_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.FAILED,
        )

    def test_foreign_source_result_is_rejected(self):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        foreign_dataset = self.create_dataset(
            self.mosmed,
            "mosmed/lungs",
            "MosMed lungs",
        )
        self.service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )

        with self.assertRaises(
            SearchResultPersistenceError
        ):
            self.service.complete_provider(
                provider_run.pk,
                source_dataset_ids=(foreign_dataset.pk,),
            )

        self.assertFalse(SearchResult.objects.exists())
        provider_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.RUNNING,
        )

    @patch("apps.search.tasks.expire_search_run.apply_async")
    @patch("apps.search.tasks.search_provider.apply_async")
    def test_enqueue_run_publishes_stored_task_id_and_deadline(
        self,
        provider_apply_async,
        deadline_apply_async,
    ):
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()

        self.service.enqueue_run(search_run.pk)

        provider_apply_async.assert_called_once_with(
            args=(provider_run.pk,),
            task_id=str(provider_run.task_id),
            queue=settings.SEARCH_TASK_QUEUE,
        )
        deadline_apply_async.assert_called_once()
        deadline_kwargs = deadline_apply_async.call_args.kwargs
        self.assertEqual(
            deadline_kwargs["args"],
            (str(search_run.pk),),
        )
        self.assertGreater(deadline_kwargs["countdown"], 0)
        self.assertTrue(deadline_kwargs["task_id"])
        self.assertEqual(
            deadline_kwargs["queue"],
            settings.SEARCH_TASK_QUEUE,
        )

    @patch("apps.search.tasks.expire_search_run.apply_async")
    @patch("apps.search.tasks.search_provider.apply_async")
    def test_broker_publish_failure_is_persisted(
        self,
        provider_apply_async,
        deadline_apply_async,
    ):
        provider_apply_async.side_effect = OperationalError(
            "redis unavailable"
        )
        search_run = self.service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )

        self.service.enqueue_run(search_run.pk)

        deadline_apply_async.assert_called_once()
        provider_run = search_run.provider_runs.get()
        provider_run.refresh_from_db()
        search_run.refresh_from_db()
        self.assertEqual(
            provider_run.status,
            SearchProviderStatus.FAILED,
        )
        self.assertEqual(
            provider_run.error_code,
            "broker_publish_failed",
        )
        self.assertEqual(
            search_run.status,
            SearchRunStatus.FAILED,
        )

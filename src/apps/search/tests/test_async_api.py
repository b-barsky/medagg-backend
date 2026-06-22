from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.search.models import SearchRunStatus
from apps.search.services import SearchRunService


class AsyncSearchApiTests(APITestCase):
    search_url = "/api/v1/search/datasets/"

    @classmethod
    def setUpTestData(cls):
        cls.kaggle = DataSource.objects.get(slug="kaggle")
        cls.kaggle.is_enabled = True
        cls.kaggle.save(
            update_fields=("is_enabled", "updated_at")
        )

    @patch(
        "apps.search.api.v1.views."
        "SearchRunService.enqueue_run"
    )
    def test_create_returns_durable_queued_receipt(
        self,
        enqueue_run,
    ):
        response = self.client.post(
            self.search_url,
            {
                "query": "lung cancer",
                "sources": ["kaggle"],
            },
            format="json",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertEqual(response.data["status"], "queued")
        self.assertFalse(response.data["is_terminal"])
        self.assertEqual(response.data["results"]["count"], 0)
        self.assertEqual(response.data["results"]["items"], [])
        self.assertIn("Location", response)
        self.assertIn("Retry-After", response)
        enqueue_run.assert_called_once()

    def test_poll_returns_summaries_while_details_are_pending(self):
        service = SearchRunService()
        search_run = service.create_run(
            "lung cancer",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )
        dataset = SourceDataset.objects.create(
            source=self.kaggle,
            external_id="owner/lung-cancer",
            source_url=(
                "https://www.kaggle.com/datasets/"
                "owner/lung-cancer"
            ),
            title="Lung cancer summary",
            detail_status=MetadataStatus.PENDING,
        )
        service.publish_provider_results(
            provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )

        response = self.client.get(
            f"{self.search_url}{search_run.pk}/",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["status"], "running")
        provider = response.data["providers"][0]
        self.assertEqual(provider["status"], "enriching")
        self.assertEqual(provider["result_count"], 1)
        self.assertEqual(provider["detail_pending_count"], 1)
        item = response.data["results"]["items"][0]
        self.assertEqual(item["id"], dataset.pk)
        self.assertEqual(item["enrichment_status"], "queued")
        self.assertEqual(item["detail_status"], "pending")

    def test_poll_returns_complete_metadata_after_enrichment(self):
        service = SearchRunService()
        search_run = service.create_run(
            "lung cancer",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )
        dataset = SourceDataset.objects.create(
            source=self.kaggle,
            external_id="owner/lung-cancer",
            source_url=(
                "https://www.kaggle.com/datasets/"
                "owner/lung-cancer"
            ),
            title="Lung cancer summary",
            detail_status=MetadataStatus.PENDING,
        )
        service.publish_provider_results(
            provider_run.pk,
            source_dataset_ids=(dataset.pk,),
        )
        result = provider_run.search_results.get()
        service.start_result_enrichment(
            result.pk,
            task_id=str(result.enrichment_task_id),
        )
        dataset.detail_status = MetadataStatus.COMPLETE
        dataset.description = "Complete Kaggle description"
        dataset.license_names = ["CC BY 4.0"]
        dataset.license_name = "CC BY 4.0"
        dataset.save(
            update_fields=(
                "detail_status",
                "description",
                "license_names",
                "license_name",
                "updated_at",
            )
        )
        service.complete_result_enrichment(result.pk)

        response = self.client.get(
            f"{self.search_url}{search_run.pk}/",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            response.data["status"],
            SearchRunStatus.COMPLETED,
        )
        self.assertTrue(response.data["is_terminal"])
        item = response.data["results"]["items"][0]
        self.assertEqual(
            item["description"],
            "Complete Kaggle description",
        )
        self.assertEqual(
            item["enrichment_status"],
            "succeeded",
        )
        self.assertEqual(item["detail_status"], "complete")
        self.assertEqual(item["license_names"], ["CC BY 4.0"])

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import DataSource, SourceDataset
from apps.search.models import (
    SearchProviderStatus,
    SearchRunStatus,
)
from apps.search.services import SearchRunService


class SearchDatasetApiTests(APITestCase):
    search_url = "/api/v1/search/datasets/"

    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.get(slug="kaggle")
        cls.source.is_enabled = True
        cls.source.save(
            update_fields=("is_enabled", "updated_at")
        )

    @staticmethod
    def create_dataset(index: int) -> SourceDataset:
        return SourceDataset.objects.create(
            source=SearchDatasetApiTests.source,
            external_id=f"owner/lung-data-{index}",
            source_url=(
                "https://www.kaggle.com/datasets/"
                f"owner/lung-data-{index}"
            ),
            title=f"Lung Data {index:02d}",
            owner_name="Owner",
            total_bytes=index * 1024,
            download_count=index,
        )

    @patch(
        "apps.search.api.v1.views."
        "SearchRunService.enqueue_run"
    )
    def test_post_creates_durable_run_and_returns_202(
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
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertEqual(response.data["query"], "lung cancer")
        self.assertEqual(
            response.data["status"],
            SearchRunStatus.QUEUED,
        )
        self.assertFalse(response.data["is_terminal"])
        self.assertEqual(
            response.data["providers"][0]["status"],
            SearchProviderStatus.QUEUED,
        )
        self.assertEqual(
            response.data["results"]["items"],
            [],
        )
        self.assertEqual(response.data["results"]["count"], 0)
        self.assertIn("Location", response)
        self.assertIn("Retry-After", response)
        self.assertEqual(
            response["Cache-Control"],
            "no-store",
        )
        enqueue_run.assert_called_once()

    def test_post_rejects_unavailable_source(self):
        response = self.client.post(
            self.search_url,
            {
                "query": "lung cancer",
                "sources": ["unknown"],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("sources", response.data)

    def test_post_rejects_short_query(self):
        response = self.client.post(
            self.search_url,
            {
                "query": "x",
                "sources": ["kaggle"],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_poll_returns_completed_paginated_results(self):
        service = SearchRunService()
        search_run = service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        service.start_provider(
            provider_run.pk,
            task_id=str(provider_run.task_id),
        )
        datasets = [
            self.create_dataset(index)
            for index in range(21)
        ]
        service.complete_provider(
            provider_run.pk,
            source_dataset_ids=tuple(
                dataset.pk
                for dataset in datasets
            ),
        )

        first_page = self.client.get(
            f"{self.search_url}{search_run.pk}/"
        )
        second_page = self.client.get(
            f"{self.search_url}{search_run.pk}/?page=2"
        )

        self.assertEqual(
            first_page.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            first_page.data["status"],
            SearchRunStatus.COMPLETED,
        )
        self.assertTrue(first_page.data["is_terminal"])
        self.assertIsNone(first_page.data["poll_after_ms"])
        self.assertEqual(
            first_page.data["results"]["count"],
            21,
        )
        self.assertEqual(
            len(first_page.data["results"]["items"]),
            20,
        )
        self.assertIsNotNone(
            first_page.data["results"]["next"]
        )
        self.assertEqual(
            first_page.data["results"]["items"][0]["source"]["slug"],
            "kaggle",
        )
        self.assertEqual(
            second_page.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            len(second_page.data["results"]["items"]),
            1,
        )
        self.assertEqual(
            first_page["Cache-Control"],
            "no-store",
        )

    def test_poll_exposes_provider_failure(self):
        service = SearchRunService()
        search_run = service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        provider_run = search_run.provider_runs.get()
        service.fail_provider(
            provider_run.pk,
            error_code="provider_unavailable",
            error_message="Kaggle is down",
        )

        response = self.client.get(
            f"{self.search_url}{search_run.pk}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            response.data["status"],
            SearchRunStatus.FAILED,
        )
        self.assertEqual(
            response.data["providers"][0]["error"],
            {
                "code": "provider_unavailable",
                "message": "Kaggle is down",
            },
        )

    def test_poll_expires_overdue_run(self):
        service = SearchRunService()
        search_run = service.create_run(
            "lungs",
            source_slugs=("kaggle",),
        )
        search_run.deadline_at = (
            timezone.now() - timedelta(seconds=1)
        )
        search_run.save(
            update_fields=("deadline_at", "updated_at")
        )

        response = self.client.get(
            f"{self.search_url}{search_run.pk}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            response.data["status"],
            SearchRunStatus.FAILED,
        )
        self.assertEqual(
            response.data["providers"][0]["error"]["code"],
            "search_deadline_exceeded",
        )

    def test_unknown_run_returns_404(self):
        response = self.client.get(
            f"{self.search_url}{uuid4()}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import DataSource
from apps.search.services import SearchRunService


class SearchBrowsableApiTests(APITestCase):
    search_url = "/api/v1/search/datasets/"

    @classmethod
    def setUpTestData(cls):
        DataSource.objects.update_or_create(
            slug="kaggle",
            defaults={
                "name": "Kaggle",
                "base_url": "https://www.kaggle.com",
                "is_enabled": True,
            },
        )

    @patch(
        "apps.search.api.v1.views."
        "SearchRunService.enqueue_run"
    )
    def test_create_can_render_html_response(
        self,
        enqueue_run,
    ):
        """
        Creating a search through the browsable API must not fail
        while DRF evaluates queryset, filtering, or pagination.
        """

        response = self.client.post(
            self.search_url,
            {
                "query": "lung cancer",
                "sources": ["kaggle"],
            },
            format="json",
            HTTP_ACCEPT="text/html",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertTrue(
            response["Content-Type"].startswith(
                "text/html"
            )
        )

        enqueue_run.assert_called_once()

    def test_retrieve_can_render_html_response(self):
        """
        Polling a search through the browsable API must also render
        successfully.
        """

        search_run = SearchRunService().create_run(
            query="lung cancer",
            source_slugs=("kaggle",),
        )

        response = self.client.get(
            f"{self.search_url}{search_run.pk}/",
            HTTP_ACCEPT="text/html",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertTrue(
            response["Content-Type"].startswith(
                "text/html"
            )
        )
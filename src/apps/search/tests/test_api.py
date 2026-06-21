from urllib.parse import urlencode

from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.models import (
    AnatomicalArea,
    Dataset,
    DatasetTag,
    Tag,
)


class SearchDatasetApiTests(APITestCase):
    search_url = "/api/v1/search/datasets/"

    @classmethod
    def setUpTestData(cls):
        moscow = AnatomicalArea.objects.create(
            name="Moscow"
        )
        berlin = AnatomicalArea.objects.create(
            name="Berlin"
        )

        cancer = Tag.objects.create(name="cancer")
        smoking = Tag.objects.create(name="smoking")

        cls.alpha = Dataset.objects.create(
            title="Alpha lung cancer study",
            description="Moscow smoking and cancer cohort",
            record_count=100,
            size=500,
            anatomical_area=moscow,
        )
        DatasetTag.objects.create(
            dataset=cls.alpha,
            tag=cancer,
        )
        DatasetTag.objects.create(
            dataset=cls.alpha,
            tag=smoking,
        )

        cls.zulu = Dataset.objects.create(
            title="Zulu lung smoking study",
            description="Moscow smoking cohort",
            record_count=0,
            size=0,
            anatomical_area=moscow,
        )
        DatasetTag.objects.create(
            dataset=cls.zulu,
            tag=smoking,
        )

        Dataset.objects.create(
            title="Berlin brain dataset",
            description="Neurological imaging",
            record_count=50,
            size=250,
            anatomical_area=berlin,
        )

    def search(self, query="lung", **parameters):
        url = self.search_url

        if parameters:
            url = f"{url}?{urlencode(parameters)}"

        return self.client.post(
            url,
            {"query": query},
            format="json",
        )

    def test_search_response_uses_standard_pagination(self):
        response = self.search()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["count"], 2)
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertIn("results", response.data)

    def test_zero_is_a_valid_numeric_filter(self):
        response = self.search(size_max=0)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["id"],
            self.zulu.id,
        )

    def test_list_filter_results_are_distinct(self):
        response = self.search(
            tags_list="cancer,smoking"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["count"], 2)

        identifiers = [
            item["id"]
            for item in response.data["results"]
        ]

        self.assertEqual(
            len(identifiers),
            len(set(identifiers)),
        )

    def test_search_can_order_by_allowlisted_field(self):
        response = self.search(ordering="title")

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        titles = [
            item["title"]
            for item in response.data["results"]
        ]

        self.assertEqual(titles, sorted(titles))

    def test_invalid_ordering_is_rejected(self):
        response = self.search(ordering="password")

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_inverted_range_is_rejected(self):
        response = self.search(
            record_count_min=100,
            record_count_max=10,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_anatomical_area_filter_is_case_insensitive(self):
        response = self.search(
            anatomical_area_name="moscow"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["count"], 2)

    def test_short_query_is_rejected(self):
        response = self.search(query="x")

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
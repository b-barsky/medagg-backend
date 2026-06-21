from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.models import (
    AnatomicalArea,
    Dataset,
    DatasetTag,
    Tag,
)


class DatasetApiTests(APITestCase):
    datasets_url = "/api/v1/datasets/"

    @classmethod
    def setUpTestData(cls):
        area = AnatomicalArea.objects.create(
            name="Moscow"
        )
        tag = Tag.objects.create(name="cancer")
        base_time = timezone.now()

        for index in range(21):
            dataset = Dataset.objects.create(
                title=f"Dataset {index:02d}",
                description="Dataset description",
                record_count=index,
                size=index,
                license="CC BY 4.0",
                anatomical_area=area,
                created_at=base_time
                + timedelta(seconds=index),
            )

            if index == 0:
                cls.dataset = dataset

                DatasetTag.objects.create(
                    dataset=dataset,
                    tag=tag,
                )

    def test_dataset_list_is_paginated(self):
        response = self.client.get(self.datasets_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["count"], 21)
        self.assertEqual(
            len(response.data["results"]),
            20,
        )
        self.assertIsNotNone(response.data["next"])
        self.assertIsNone(response.data["previous"])

    def test_second_dataset_page_contains_remaining_item(self):
        response = self.client.get(
            f"{self.datasets_url}?page=2"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            len(response.data["results"]),
            1,
        )
        self.assertIsNone(response.data["next"])
        self.assertIsNotNone(response.data["previous"])

    def test_dataset_detail_contains_nested_tags(self):
        response = self.client.get(
            f"{self.datasets_url}{self.dataset.id}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["id"], self.dataset.id)
        self.assertEqual(
            response.data["tags"][0]["name"],
            "cancer",
        )

    def test_unknown_dataset_returns_404(self):
        response = self.client.get(
            f"{self.datasets_url}999999/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
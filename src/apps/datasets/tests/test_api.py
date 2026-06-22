from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.models import (
    AnatomicalArea,
    Dataset,
    DatasetArtifact,
    DatasetArtifactKind,
    DatasetOrigin,
    DatasetTag,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibility,
    Tag,
)


class DatasetApiTests(APITestCase):
    datasets_url = "/api/v1/datasets/"

    @classmethod
    def setUpTestData(cls):
        area = AnatomicalArea.objects.create(name="Moscow")
        tag = Tag.objects.create(name="cancer")

        for index in range(21):
            dataset = Dataset.objects.create(
                origin=DatasetOrigin.MANUAL,
                visibility=DatasetVisibility.PUBLIC,
                title=f"Dataset {index:02d}",
                description="Dataset description",
                record_count=index,
                size_bytes=index * 1024,
                license="CC BY 4.0",
                license_names=["CC BY 4.0"],
                anatomical_area=area,
            )
            version = DatasetVersion.objects.create(
                dataset=dataset,
                number=1,
                status=DatasetVersionStatus.AVAILABLE,
                checksum_sha256=f"{index:064x}",
                size_bytes=index * 1024,
            )

            if index == 0:
                cls.dataset = dataset
                DatasetTag.objects.create(
                    dataset=dataset,
                    tag=tag,
                )
                DatasetArtifact.objects.create(
                    dataset_version=version,
                    kind=DatasetArtifactKind.SOURCE_ARCHIVE,
                    bucket="test-datasets",
                    object_key="datasets/first.zip",
                    filename="first.zip",
                    content_type="application/zip",
                    size_bytes=0,
                    checksum_sha256="0" * 64,
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

    def test_dataset_detail_contains_nested_tags_and_versions(self):
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
        self.assertEqual(
            response.data["versions"][0]["status"],
            DatasetVersionStatus.AVAILABLE,
        )
        self.assertEqual(
            response.data["versions"][0]["artifacts"][0]["filename"],
            "first.zip",
        )

    def test_dataset_without_available_version_is_hidden(self):
        dataset = Dataset.objects.create(
            origin=DatasetOrigin.MANUAL,
            visibility=DatasetVisibility.PUBLIC,
            title="Staging only",
        )
        DatasetVersion.objects.create(
            dataset=dataset,
            number=1,
            status=DatasetVersionStatus.STAGING,
        )

        response = self.client.get(
            f"{self.datasets_url}{dataset.pk}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_unknown_dataset_returns_404(self):
        response = self.client.get(
            f"{self.datasets_url}999999/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

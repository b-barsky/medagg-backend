from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.models import (
    AnatomicalArea,
    Dataset,
    DatasetArtifact,
    DatasetArtifactKind,
    DatasetMembership,
    DatasetOrigin,
    DatasetTag,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibility,
    Tag,
)


User = get_user_model()


class DatasetApiTests(APITestCase):
    datasets_url = "/api/v1/datasets/"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="library-owner",
            password="test-password",
        )
        cls.other_user = User.objects.create_user(
            username="other-owner",
            password="test-password",
        )
        area = AnatomicalArea.objects.create(name="Moscow")
        tag = Tag.objects.create(name="cancer")

        for index in range(21):
            dataset = Dataset.objects.create(
                origin=DatasetOrigin.MANUAL,
                visibility=DatasetVisibility.INTERNAL,
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
            DatasetMembership.objects.create(
                user=cls.user,
                dataset=dataset,
            )

            if index == 0:
                cls.dataset = dataset
                DatasetTag.objects.create(dataset=dataset, tag=tag)
                cls.artifact = DatasetArtifact.objects.create(
                    dataset_version=version,
                    kind=DatasetArtifactKind.SOURCE_ARCHIVE,
                    bucket="test-datasets",
                    object_key="datasets/first.zip",
                    filename="first.zip",
                    content_type="application/zip",
                    size_bytes=0,
                    checksum_sha256="0" * 64,
                )

        cls.other_dataset = Dataset.objects.create(
            origin=DatasetOrigin.MANUAL,
            visibility=DatasetVisibility.INTERNAL,
            title="Another user's dataset",
        )
        DatasetVersion.objects.create(
            dataset=cls.other_dataset,
            number=1,
            status=DatasetVersionStatus.AVAILABLE,
            checksum_sha256="f" * 64,
            size_bytes=1,
        )
        DatasetMembership.objects.create(
            user=cls.other_user,
            dataset=cls.other_dataset,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_anonymous_user_cannot_list_library(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.datasets_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_dataset_list_contains_only_current_user_memberships(self):
        response = self.client.get(self.datasets_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 21)
        identifiers = {
            item["id"] for item in response.data["results"]
        }
        self.assertNotIn(self.other_dataset.pk, identifiers)

    def test_dataset_list_is_paginated(self):
        first = self.client.get(self.datasets_url)
        second = self.client.get(f"{self.datasets_url}?page=2")

        self.assertEqual(len(first.data["results"]), 20)
        self.assertEqual(len(second.data["results"]), 1)
        self.assertIsNotNone(first.data["next"])
        self.assertIsNotNone(second.data["previous"])

    def test_dataset_detail_contains_membership_and_artifacts(self):
        response = self.client.get(
            f"{self.datasets_url}{self.dataset.pk}/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tags"][0]["name"], "cancer")
        self.assertIsNotNone(response.data["added_at"])
        self.assertEqual(response.data["acquisition"], "imported")
        self.assertEqual(
            response.data["versions"][0]["artifacts"][0]["filename"],
            "first.zip",
        )

    def test_other_users_dataset_is_not_visible(self):
        response = self.client.get(
            f"{self.datasets_url}{self.other_dataset.pk}/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_dataset_without_available_version_is_hidden(self):
        dataset = Dataset.objects.create(
            origin=DatasetOrigin.MANUAL,
            visibility=DatasetVisibility.INTERNAL,
            title="Staging only",
        )
        DatasetVersion.objects.create(
            dataset=dataset,
            number=1,
            status=DatasetVersionStatus.STAGING,
        )
        DatasetMembership.objects.create(user=self.user, dataset=dataset)

        response = self.client.get(
            f"{self.datasets_url}{dataset.pk}/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

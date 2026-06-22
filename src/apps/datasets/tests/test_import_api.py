from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.datasets.models import (
    Dataset,
    DatasetArtifact,
    DatasetArtifactKind,
    DatasetOrigin,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibility,
)
from apps.datasets.policies import DatasetImportPolicy


@override_settings(
    DATASET_IMPORT_REQUIRE_AUTHENTICATION=False,
    DATASET_IMPORT_MAX_BYTES=10_000,
    DATASET_IMPORT_ALLOW_PRIVATE=False,
    DATASET_IMPORT_ALLOWED_LICENSES=frozenset({"cc0-1.0"}),
    DATASET_IMPORT_POLL_INTERVAL_MS=1500,
)
class DatasetImportApiTests(APITestCase):
    imports_url = "/api/v1/datasets/imports/"

    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.update_or_create(
            slug="kaggle",
            defaults={
                "name": "Kaggle",
                "base_url": "https://www.kaggle.com",
                "is_enabled": True,
            },
        )[0]
        cls.source_dataset = SourceDataset.objects.create(
            source=source,
            external_id="owner/lung-data",
            source_url=(
                "https://www.kaggle.com/datasets/owner/lung-data"
            ),
            title="Lung data",
            license_name="CC0: Public Domain",
            license_names=["CC0: Public Domain"],
            total_bytes=1024,
            remote_version="1",
            detail_status=MetadataStatus.COMPLETE,
        )
        cls.staff_user = get_user_model().objects.create_user(
            username="artifact-admin",
            password="test-password",
            is_staff=True,
        )

    def payload(self):
        decision = DatasetImportPolicy().evaluate(
            self.source_dataset
        )
        return {
            "source_dataset_id": self.source_dataset.pk,
            "accept_license": True,
            "license_fingerprint": (
                decision.license_fingerprint
            ),
        }

    @patch(
        "apps.datasets.api.v1.views."
        "DatasetImportService.enqueue_import",
        return_value="task-1",
    )
    def test_create_returns_202_and_pollable_run(self, enqueue):
        response = self.client.post(
            self.imports_url,
            self.payload(),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertEqual(response.data["status"], "queued")
        self.assertFalse(response.data["is_terminal"])
        self.assertEqual(response.data["poll_after_ms"], 1500)
        self.assertIn("Location", response)
        enqueue.assert_called_once()

        poll = self.client.get(
            f"{self.imports_url}{response.data['id']}/"
        )

        self.assertEqual(
            poll.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(poll.data["id"], response.data["id"])

    def test_license_must_be_accepted(self):
        payload = self.payload()
        payload["accept_license"] = False

        response = self.client.post(
            self.imports_url,
            payload,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_stale_license_fingerprint_returns_conflict(self):
        payload = self.payload()
        payload["license_fingerprint"] = "0" * 64

        response = self.client.post(
            self.imports_url,
            payload,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_409_CONFLICT,
        )
        self.assertEqual(
            response.data["policy"]["code"],
            "license_changed",
        )

    @override_settings(DATASET_IMPORT_ALLOW_PRIVATE=True)
    def test_private_import_requires_staff_user(self):
        self.source_dataset.is_private = True
        self.source_dataset.save(update_fields=("is_private",))

        response = self.client.post(
            self.imports_url,
            self.payload(),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_409_CONFLICT,
        )
        self.assertEqual(
            response.data["policy"]["code"],
            "private_access_denied",
        )

    @override_settings(DATASET_IMPORT_ALLOW_PRIVATE=True)
    @patch(
        "apps.datasets.api.v1.views."
        "DatasetImportService.enqueue_import",
        return_value="task-private",
    )
    def test_staff_user_can_request_allowed_private_import(self, enqueue):
        self.source_dataset.is_private = True
        self.source_dataset.save(update_fields=("is_private",))
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.post(
            self.imports_url,
            self.payload(),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )
        enqueue.assert_called_once()

    @override_settings(DATASET_IMPORT_REQUIRE_AUTHENTICATION=True)
    def test_authentication_can_be_required(self):
        response = self.client.post(
            self.imports_url,
            self.payload(),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )


@override_settings(
    DATASET_IMPORT_REQUIRE_AUTHENTICATION=False,
    OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS=900,
)
class DatasetArtifactApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        dataset = Dataset.objects.create(
            origin=DatasetOrigin.IMPORTED,
            visibility=DatasetVisibility.INTERNAL,
            title="Imported dataset",
            size_bytes=3,
        )
        version = DatasetVersion.objects.create(
            dataset=dataset,
            number=1,
            status=DatasetVersionStatus.AVAILABLE,
            checksum_sha256="a" * 64,
            size_bytes=3,
        )
        cls.artifact = DatasetArtifact.objects.create(
            dataset_version=version,
            kind=DatasetArtifactKind.SOURCE_ARCHIVE,
            bucket="test-datasets",
            object_key="datasets/1/archive.zip",
            object_version_id="version-1",
            filename="archive.zip",
            content_type="application/zip",
            size_bytes=3,
            checksum_sha256="a" * 64,
        )

    @patch(
        "apps.datasets.api.v1.views."
        "S3ObjectStorage.presigned_download_url",
        return_value="http://storage.example/archive.zip?signature=1",
    )
    def test_download_endpoint_returns_short_lived_url(self, presign):
        response = self.client.get(
            "/api/v1/datasets/artifacts/"
            f"{self.artifact.pk}/download/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data["expires_in"], 900)
        self.assertEqual(
            response.data["checksum_sha256"],
            "a" * 64,
        )
        presign.assert_called_once_with(
            bucket="test-datasets",
            object_key="datasets/1/archive.zip",
            object_version_id="version-1",
            filename="archive.zip",
        )

    def test_artifact_collection_is_not_exposed(self):
        response = self.client.get(
            "/api/v1/datasets/artifacts/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.builder.models import (
    AnalysisStatus,
    BuilderModelRelease,
    DatasetAnalysis,
)
from apps.datasets.models import (
    Dataset,
    DatasetMembership,
    DatasetVersion,
    DatasetVersionStatus,
)


User = get_user_model()


class BuilderApiTests(APITestCase):
    requests_url = "/api/v1/builder/requests/"
    library_url = "/api/v1/builder/library/"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="builder-user",
            password="safe-test-password",
        )
        cls.other_user = User.objects.create_user(
            username="other-user",
            password="safe-test-password",
        )
        release = BuilderModelRelease.objects.create(
            version="api-test-model",
            training_data_checksum="a" * 64,
            artifact_checksum="b" * 64,
            artifact=b"test",
            is_active=True,
        )
        cls.own_dataset = Dataset.objects.create(title="Authorized dataset")
        own_version = DatasetVersion.objects.create(
            dataset=cls.own_dataset,
            number=1,
            status=DatasetVersionStatus.AVAILABLE,
        )
        cls.own_analysis = DatasetAnalysis.objects.create(
            dataset_version=own_version,
            model_release=release,
            status=AnalysisStatus.COMPLETE,
            schema_fingerprint="a" * 64,
        )
        DatasetMembership.objects.create(
            user=cls.user,
            dataset=cls.own_dataset,
        )

        other_dataset = Dataset.objects.create(title="Another user's dataset")
        other_version = DatasetVersion.objects.create(
            dataset=other_dataset,
            number=1,
            status=DatasetVersionStatus.AVAILABLE,
        )
        cls.other_analysis = DatasetAnalysis.objects.create(
            dataset_version=other_version,
            model_release=release,
            status=AnalysisStatus.COMPLETE,
            schema_fingerprint="b" * 64,
        )
        DatasetMembership.objects.create(
            user=cls.other_user,
            dataset=other_dataset,
        )

    def test_builder_library_requires_authentication(self):
        response = self.client.get(self.library_url)
        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_library_contains_only_membership_authorized_datasets(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(self.library_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["dataset"]["id"],
            self.own_dataset.pk,
        )

    def test_analysis_from_another_users_library_is_hidden(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(
            f"/api/v1/builder/analyses/{self.other_analysis.pk}/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("apps.builder.api.v1.views.enqueue_build_request")
    def test_authenticated_user_can_create_durable_request(self, enqueue):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.requests_url,
            {
                "prompt": "Combine lung cancer and smoking records",
                "purpose": "Internal medical research validation",
                "privacy_acknowledged": True,
                "dataset_ids": [],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], "queued")
        enqueue.assert_called_once()

    @patch("apps.builder.api.v1.views.enqueue_build_request")
    def test_privacy_acknowledgement_is_required(self, enqueue):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.requests_url,
            {
                "prompt": "Combine compatible records",
                "purpose": "Internal medical research validation",
                "privacy_acknowledged": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        enqueue.assert_not_called()

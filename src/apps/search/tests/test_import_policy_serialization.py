from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.search.api.v1.serializers import (
    SourceDatasetSummarySerializer,
)


@override_settings(
    DATASET_IMPORT_MAX_BYTES=10_000,
    DATASET_IMPORT_ALLOW_PRIVATE=False,
    DATASET_IMPORT_ALLOWED_LICENSES=frozenset({"cc0-1.0"}),
)
class SearchImportPolicySerializationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.update_or_create(
            slug="kaggle",
            defaults={
                "name": "Kaggle",
                "base_url": "https://www.kaggle.com",
                "is_enabled": True,
            },
        )[0]

    def create_source_dataset(self, external_id: str) -> SourceDataset:
        return SourceDataset.objects.create(
            source=self.source,
            external_id=external_id,
            source_url=(
                "https://www.kaggle.com/datasets/" + external_id
            ),
            title="Lung data",
            license_name="CC0: Public Domain",
            license_names=["CC0: Public Domain"],
            total_bytes=1024,
            remote_version="1",
            detail_status=MetadataStatus.COMPLETE,
        )

    def test_authenticated_result_can_request_import(self):
        source_dataset = self.create_source_dataset("owner/lung-data")
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.create_user(
            username="search-user",
            password="test-password",
        )

        policy = SourceDatasetSummarySerializer(
            source_dataset,
            context={"request": request},
        ).data["import_policy"]

        self.assertTrue(policy["eligible"])
        self.assertFalse(policy["allowed"])
        self.assertTrue(policy["can_request_import"])
        self.assertTrue(policy["authentication_required"])
        self.assertEqual(
            policy["code"],
            "license_acceptance_required",
        )
        self.assertEqual(len(policy["license_fingerprint"]), 64)

    def test_anonymous_result_requires_sign_in(self):
        source_dataset = self.create_source_dataset(
            "owner/anonymous-data"
        )
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        policy = SourceDatasetSummarySerializer(
            source_dataset,
            context={"request": request},
        ).data["import_policy"]

        self.assertTrue(policy["eligible"])
        self.assertFalse(policy["can_request_import"])
        self.assertTrue(policy["authentication_required"])
        self.assertEqual(
            policy["request_message"],
            "Sign in before importing this dataset.",
        )

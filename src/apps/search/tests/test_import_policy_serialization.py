from django.test import TestCase, override_settings

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
    DATASET_IMPORT_REQUIRE_AUTHENTICATION=False,
    DATASET_IMPORT_ALLOWED_LICENSES=frozenset({"cc0-1.0"}),
)
class SearchImportPolicySerializationTests(TestCase):
    def test_search_result_contains_import_policy_fingerprint(self):
        source = DataSource.objects.update_or_create(
            slug="kaggle",
            defaults={
                "name": "Kaggle",
                "base_url": "https://www.kaggle.com",
                "is_enabled": True,
            },
        )[0]
        source_dataset = SourceDataset.objects.create(
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

        data = SourceDatasetSummarySerializer(
            source_dataset
        ).data
        policy = data["import_policy"]

        self.assertTrue(policy["eligible"])
        self.assertFalse(policy["allowed"])
        self.assertTrue(policy["can_request_import"])
        self.assertFalse(policy["authentication_required"])
        self.assertEqual(
            policy["code"],
            "license_acceptance_required",
        )
        self.assertEqual(
            len(policy["license_fingerprint"]),
            64,
        )

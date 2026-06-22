from django.test import TestCase, override_settings

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.datasets.policies import DatasetImportPolicy


@override_settings(
    DATASET_IMPORT_MAX_BYTES=10_000,
    DATASET_IMPORT_ALLOW_PRIVATE=False,
    DATASET_IMPORT_ALLOWED_LICENSES=frozenset({"cc0-1.0"}),
)
class DatasetImportPolicyTests(TestCase):
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
        cls.source_dataset = SourceDataset.objects.create(
            source=cls.source,
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

    def setUp(self):
        self.policy = DatasetImportPolicy()
        self.source_dataset.refresh_from_db()

    def test_eligible_dataset_requires_explicit_acceptance(self):
        decision = self.policy.evaluate(self.source_dataset)

        self.assertTrue(decision.eligible)
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.code,
            "license_acceptance_required",
        )
        self.assertTrue(decision.requires_license_acceptance)
        self.assertEqual(
            decision.canonical_licenses,
            ("cc0-1.0",),
        )

    def test_exact_fingerprint_allows_import(self):
        preview = self.policy.evaluate(self.source_dataset)

        decision = self.policy.evaluate(
            self.source_dataset,
            accepted_license=True,
            presented_license_fingerprint=(
                preview.license_fingerprint
            ),
        )

        self.assertTrue(decision.eligible)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, "allowed")

    def test_changed_revision_invalidates_acceptance(self):
        preview = self.policy.evaluate(self.source_dataset)
        self.source_dataset.remote_version = "2"
        self.source_dataset.save(update_fields=("remote_version",))

        decision = self.policy.evaluate(
            self.source_dataset,
            accepted_license=True,
            presented_license_fingerprint=(
                preview.license_fingerprint
            ),
        )

        self.assertTrue(decision.eligible)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "license_changed")

    def test_incomplete_metadata_is_rejected(self):
        self.source_dataset.detail_status = MetadataStatus.PENDING
        self.source_dataset.save(update_fields=("detail_status",))

        decision = self.policy.evaluate(self.source_dataset)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.code, "metadata_incomplete")

    def test_private_dataset_is_rejected(self):
        self.source_dataset.is_private = True
        self.source_dataset.save(update_fields=("is_private",))

        decision = self.policy.evaluate(self.source_dataset)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.code, "private_dataset")

    @override_settings(DATASET_IMPORT_ALLOW_PRIVATE=True)
    def test_private_dataset_requires_staff_authorization(self):
        self.source_dataset.is_private = True
        self.source_dataset.save(update_fields=("is_private",))

        decision = self.policy.evaluate(self.source_dataset)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.code, "private_access_denied")

    @override_settings(DATASET_IMPORT_ALLOW_PRIVATE=True)
    def test_staff_authorized_private_dataset_can_reach_acceptance(self):
        self.source_dataset.is_private = True
        self.source_dataset.save(update_fields=("is_private",))

        decision = self.policy.evaluate(
            self.source_dataset,
            private_access_authorized=True,
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(
            decision.code,
            "license_acceptance_required",
        )
        self.assertTrue(decision.private_access_authorized)

    def test_unknown_size_is_rejected(self):
        self.source_dataset.total_bytes = None
        self.source_dataset.save(update_fields=("total_bytes",))

        decision = self.policy.evaluate(self.source_dataset)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.code, "size_unknown")

    def test_provider_size_over_limit_is_rejected(self):
        self.source_dataset.total_bytes = 10_001
        self.source_dataset.save(update_fields=("total_bytes",))

        decision = self.policy.evaluate(self.source_dataset)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.code, "size_limit_exceeded")

    def test_unrecognized_license_is_rejected(self):
        self.source_dataset.license_name = "Custom research terms"
        self.source_dataset.license_names = ["Custom research terms"]
        self.source_dataset.save(
            update_fields=("license_name", "license_names")
        )

        decision = self.policy.evaluate(self.source_dataset)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.code, "license_unknown")

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.datasets.access import DatasetAccessDenied, DatasetAccessService
from apps.datasets.models import (
    Dataset,
    DatasetImport,
    DatasetImportRequester,
    DatasetImportStatus,
    DatasetMembership,
    DatasetMembershipAcquisition,
    DatasetOrigin,
    DatasetVersion,
    DatasetVersionStatus,
)


User = get_user_model()


@override_settings(
    DATASET_IMPORT_MAX_BYTES=10_000,
    DATASET_IMPORT_ALLOW_PRIVATE=False,
    DATASET_IMPORT_ALLOWED_LICENSES=frozenset({"cc0-1.0"}),
)
class DatasetAccessServiceTests(TestCase):
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
            external_id="owner/shared-data",
            source_url=(
                "https://www.kaggle.com/datasets/owner/shared-data"
            ),
            title="Shared data",
            license_name="CC0: Public Domain",
            license_names=["CC0: Public Domain"],
            total_bytes=1024,
            remote_version="1",
            detail_status=MetadataStatus.COMPLETE,
        )
        cls.first_user = User.objects.create_user(
            username="first-importer",
            password="test-password",
        )
        cls.second_user = User.objects.create_user(
            username="second-importer",
            password="test-password",
        )

    def setUp(self):
        self.service = DatasetAccessService()
        self.source_dataset.refresh_from_db()

    def request(self, user):
        from apps.datasets.policies import DatasetImportPolicy

        decision = DatasetImportPolicy().evaluate(self.source_dataset)
        return self.service.request_import(
            user=user,
            source_dataset_id=self.source_dataset.pk,
            accepted_license=True,
            license_fingerprint=decision.license_fingerprint,
        )

    @staticmethod
    def complete_import(import_run: DatasetImport):
        dataset = Dataset.objects.create(
            source_dataset=import_run.source_dataset,
            origin=DatasetOrigin.IMPORTED,
            title=import_run.source_dataset.title,
        )
        version = DatasetVersion.objects.create(
            dataset=dataset,
            number=1,
            status=DatasetVersionStatus.AVAILABLE,
            source_revision=import_run.source_revision,
            checksum_sha256="a" * 64,
            size_bytes=1024,
            available_at=timezone.now(),
        )
        import_run.dataset = dataset
        import_run.dataset_version = version
        import_run.status = DatasetImportStatus.SUCCEEDED
        import_run.finished_at = timezone.now()
        import_run.save(
            update_fields=(
                "dataset",
                "dataset_version",
                "status",
                "finished_at",
            )
        )
        return dataset, version

    def test_unauthenticated_user_is_rejected(self):
        from django.contrib.auth.models import AnonymousUser

        with self.assertRaises(DatasetAccessDenied):
            self.request(AnonymousUser())

    def test_two_users_join_one_active_import(self):
        first = self.request(self.first_user)
        second = self.request(self.second_user)

        self.assertEqual(
            first.creation.import_run.pk,
            second.creation.import_run.pk,
        )
        self.assertEqual(DatasetImport.objects.count(), 1)
        self.assertEqual(DatasetImportRequester.objects.count(), 2)
        self.assertEqual(DatasetMembership.objects.count(), 0)

        dataset, _ = self.complete_import(first.creation.import_run)
        granted = self.service.grant_import_access(
            first.creation.import_run.pk
        )

        self.assertEqual(granted, 2)
        self.assertEqual(
            DatasetMembership.objects.filter(dataset=dataset).count(),
            2,
        )
        self.assertEqual(
            DatasetMembership.objects.get(
                dataset=dataset,
                user=self.first_user,
            ).acquisition,
            DatasetMembershipAcquisition.IMPORTED,
        )
        self.assertEqual(
            DatasetMembership.objects.get(
                dataset=dataset,
                user=self.second_user,
            ).acquisition,
            DatasetMembershipAcquisition.SHARED,
        )
        self.assertFalse(
            DatasetImportRequester.objects.filter(
                import_run=first.creation.import_run,
                access_granted_at__isnull=True,
            ).exists()
        )

    def test_existing_version_is_shared_without_copying(self):
        first = self.request(self.first_user)
        dataset, version = self.complete_import(first.creation.import_run)
        self.service.grant_import_access(first.creation.import_run.pk)

        second = self.request(self.second_user)

        self.assertTrue(second.creation.reused_version)
        self.assertTrue(second.access_granted)
        self.assertEqual(Dataset.objects.count(), 1)
        self.assertEqual(DatasetVersion.objects.count(), 1)
        self.assertEqual(
            second.creation.import_run.dataset_version_id,
            version.pk,
        )
        membership = DatasetMembership.objects.get(
            user=self.second_user,
            dataset=dataset,
        )
        self.assertEqual(
            membership.acquisition,
            DatasetMembershipAcquisition.SHARED,
        )
        requester = DatasetImportRequester.objects.get(
            import_run=second.creation.import_run,
            user=self.second_user,
        )
        self.assertIsNotNone(requester.access_granted_at)

import hashlib
from pathlib import Path

from django.test import TestCase, override_settings

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.catalog.providers.base import DatasetProvider
from apps.catalog.providers.dto import (
    ProviderArtifactDownload,
    ProviderDatasetDetails,
    ProviderSearchPage,
)
from apps.catalog.providers.registry import ProviderRegistry
from apps.datasets.models import (
    Dataset,
    DatasetArtifact,
    DatasetImport,
    DatasetImportStatus,
    DatasetVersion,
    DatasetVersionStatus,
)
from apps.datasets.policies import DatasetImportPolicy
from apps.datasets.services import (
    DatasetImportPolicyRejected,
    DatasetImportService,
    DatasetImportSourceChanged,
    DatasetImportTooLarge,
)
from apps.datasets.storage import StoredObject


ARCHIVE_BYTES = b"deterministic-kaggle-archive"
ARCHIVE_SHA256 = hashlib.sha256(ARCHIVE_BYTES).hexdigest()


class FakeDownloadProvider(DatasetProvider):
    slug = "kaggle"

    def __init__(self) -> None:
        self.download_calls: list[str] = []

    def search_summary(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> ProviderSearchPage:
        return ProviderSearchPage(
            query=query,
            page=page,
            items=(),
        )

    def fetch_details(
        self,
        external_id: str,
    ) -> ProviderDatasetDetails:
        return ProviderDatasetDetails(external_id=external_id)

    def download_artifact(
        self,
        external_id: str,
        destination: Path,
    ) -> ProviderArtifactDownload:
        self.download_calls.append(external_id)
        path = destination / "lung-data.zip"
        path.write_bytes(ARCHIVE_BYTES)
        return ProviderArtifactDownload(
            path=path,
            filename=path.name,
            content_type="application/zip",
        )


class FakeObjectStorage:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def store_file(
        self,
        path: Path,
        *,
        dataset_id: int,
        filename: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> StoredObject:
        self.calls.append(
            {
                "path": path,
                "dataset_id": dataset_id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "checksum_sha256": checksum_sha256,
            }
        )
        return StoredObject(
            bucket="test-datasets",
            object_key=(
                f"datasets/{dataset_id}/sha256/"
                f"{checksum_sha256}/lung-data.zip"
            ),
            object_version_id="object-version-1",
            etag="multipart-etag",
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            reused=False,
        )

    def presigned_download_url(self, **kwargs):
        del kwargs
        return "http://storage.example/download"


@override_settings(
    DATASET_IMPORT_MAX_BYTES=10_000,
    DATASET_IMPORT_ALLOW_PRIVATE=False,
    DATASET_IMPORT_ALLOWED_LICENSES=frozenset({"cc0-1.0"}),
    DATASET_IMPORTED_VISIBILITY="internal",
)
class DatasetImportServiceTests(TestCase):
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
            description="Detailed description",
            license_name="CC0: Public Domain",
            license_names=["CC0: Public Domain"],
            total_bytes=len(ARCHIVE_BYTES),
            remote_version="1",
            detail_status=MetadataStatus.COMPLETE,
        )

    def setUp(self):
        self.source_dataset.refresh_from_db()
        self.provider = FakeDownloadProvider()
        self.storage = FakeObjectStorage()
        registry = ProviderRegistry()
        registry.register("kaggle", lambda: self.provider)
        self.service = DatasetImportService(
            registry=registry,
            storage=self.storage,
        )

    def create_import(self):
        preview = DatasetImportPolicy().evaluate(
            self.source_dataset
        )
        return self.service.create_import(
            source_dataset_id=self.source_dataset.pk,
            accepted_license=True,
            license_fingerprint=preview.license_fingerprint,
        )

    def test_import_downloads_hashes_and_materializes_version(self):
        creation = self.create_import()

        result = self.service.execute_import(
            creation.import_run.pk,
            task_id="task-1",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.checksum_sha256, ARCHIVE_SHA256)
        self.assertEqual(result.size_bytes, len(ARCHIVE_BYTES))
        self.assertEqual(
            self.provider.download_calls,
            ["owner/lung-data"],
        )

        import_run = DatasetImport.objects.get(
            pk=creation.import_run.pk
        )
        dataset = Dataset.objects.get()
        version = DatasetVersion.objects.get()
        artifact = DatasetArtifact.objects.get()

        self.assertEqual(
            import_run.status,
            DatasetImportStatus.SUCCEEDED,
        )
        self.assertEqual(
            version.status,
            DatasetVersionStatus.AVAILABLE,
        )
        self.assertEqual(version.checksum_sha256, ARCHIVE_SHA256)
        self.assertEqual(artifact.checksum_sha256, ARCHIVE_SHA256)
        self.assertEqual(dataset.source_dataset, self.source_dataset)
        self.assertEqual(dataset.size_bytes, len(ARCHIVE_BYTES))
        self.assertEqual(
            version.manifest["license"]["accepted"],
            True,
        )

    def test_repeated_import_reuses_available_version_without_download(self):
        first = self.create_import()
        self.service.execute_import(
            first.import_run.pk,
            task_id="task-1",
        )
        first.import_run.refresh_from_db()
        self.provider.download_calls.clear()

        second = self.create_import()

        self.assertFalse(second.created)
        self.assertTrue(second.reused_version)
        self.assertEqual(
            second.import_run.status,
            DatasetImportStatus.SUCCEEDED,
        )
        self.assertEqual(
            second.import_run.dataset_version_id,
            first.import_run.dataset_version_id,
        )
        self.assertEqual(self.provider.download_calls, [])
        self.assertEqual(DatasetVersion.objects.count(), 1)
        self.assertEqual(DatasetImport.objects.count(), 2)

    @override_settings(DATASET_IMPORT_MAX_BYTES=10)
    def test_actual_download_size_is_enforced_after_download(self):
        self.source_dataset.total_bytes = 1
        self.source_dataset.save(update_fields=("total_bytes",))
        creation = self.create_import()

        with self.assertRaises(DatasetImportTooLarge):
            self.service.execute_import(
                creation.import_run.pk,
                task_id="task-1",
            )

        self.assertEqual(
            self.provider.download_calls,
            ["owner/lung-data"],
        )
        self.assertEqual(self.storage.calls, [])

    def test_changed_source_revision_is_rejected_before_download(self):
        creation = self.create_import()
        self.source_dataset.remote_version = "2"
        self.source_dataset.save(update_fields=("remote_version",))

        with self.assertRaises(DatasetImportSourceChanged):
            self.service.execute_import(
                creation.import_run.pk,
                task_id="task-1",
            )

        self.assertEqual(self.provider.download_calls, [])

    def test_changed_license_is_rejected_before_download(self):
        creation = self.create_import()
        self.source_dataset.license_name = "MIT"
        self.source_dataset.license_names = ["MIT"]
        self.source_dataset.save(
            update_fields=("license_name", "license_names")
        )

        with self.assertRaises(DatasetImportPolicyRejected) as raised:
            self.service.execute_import(
                creation.import_run.pk,
                task_id="task-1",
            )

        self.assertEqual(
            raised.exception.decision.code,
            "license_changed",
        )
        self.assertEqual(self.provider.download_calls, [])

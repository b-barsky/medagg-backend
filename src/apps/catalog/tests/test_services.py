from datetime import UTC, datetime

from django.test import TestCase

from apps.catalog.models import (DataSource, MetadataStatus, SourceDataset)
from apps.catalog.providers.base import DatasetProvider
from apps.catalog.providers.dto import (ProviderDatasetDetails, ProviderDatasetSummary, ProviderSearchPage)
from apps.catalog.providers.exceptions import (ProviderResponseError, ProviderUnavailableError)
from apps.catalog.providers.registry import ProviderRegistry
from apps.catalog.services import (CatalogService, CatalogSourceUnavailableError)


class StubProvider(DatasetProvider):
    slug = "kaggle"

    def __init__(self) -> None:
        self.items: list[ProviderDatasetSummary] = []
        self.details = ProviderDatasetDetails(external_id="owner/lung-data")
        self.detail_error: Exception | None = None

        self.on_fetch = None
        self.search_calls: list[dict[str, object]] = []
        self.detail_calls: list[str] = []

    def search_summary(self, query: str, *, page: int = 1) -> ProviderSearchPage:
        self.search_calls.append({"query": query, "page": page})

        return ProviderSearchPage(query=query, page=page, items=tuple(self.items))

    def fetch_details(self, external_id: str) -> ProviderDatasetDetails:
        self.detail_calls.append(external_id)

        if self.on_fetch is not None:
            self.on_fetch()

        if self.detail_error is not None:
            raise self.detail_error

        return self.details


class CatalogServiceTests(TestCase):
    def setUp(self):
        self.source = DataSource.objects.get(slug="kaggle")
        self.source.is_enabled = True
        self.source.save(update_fields=("is_enabled", "updated_at"))

        self.provider = StubProvider()

        registry = ProviderRegistry()
        registry.register("kaggle", lambda: self.provider)

        self.service = CatalogService(registry)

    @staticmethod
    def item(title: str = "Original title", *, remote_version: str = "1",
             remote_updated_at: datetime | None = None) -> ProviderDatasetSummary:
        return ProviderDatasetSummary(external_id="owner/lung-data", source_url=("https://www.kaggle.com/datasets/"
                                                                                 "owner/lung-data"), title=title,
                                      remote_version=remote_version, remote_updated_at=remote_updated_at)

    def create_from_summary(self) -> SourceDataset:
        self.provider.items = [self.item()]
        self.service.search_and_upsert("kaggle", "lungs")
        return SourceDataset.objects.get()

    def test_repeated_sync_updates_same_row(self):
        self.provider.items = [self.item()]

        first = self.service.search_and_upsert("kaggle", "lungs")
        first_record = SourceDataset.objects.get()

        self.provider.items = [self.item("Updated title")]

        second = self.service.search_and_upsert("kaggle", "lungs")
        second_record = SourceDataset.objects.get()

        self.assertEqual(SourceDataset.objects.count(), 1)
        self.assertEqual(first_record.pk, second_record.pk)
        self.assertEqual(second_record.title, "Updated title")
        self.assertEqual(first.created_count, 1)
        self.assertEqual(first.refreshed_count, 0)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.refreshed_count, 1)

    def test_duplicate_provider_items_are_collapsed(self):
        self.provider.items = [self.item("First"), self.item("Second")]

        result = self.service.search_and_upsert("kaggle", "lungs")

        self.assertEqual(result.fetched_count, 2)
        self.assertEqual(result.stored_count, 1)
        self.assertEqual(SourceDataset.objects.get().title, "Second")

    def test_new_summary_is_pending(self):
        self.assertEqual(self.create_from_summary().detail_status, MetadataStatus.PENDING)

    def test_summary_refresh_does_not_erase_details(self):
        record = self.create_from_summary()
        record.subtitle = "Existing subtitle"
        record.description = "Existing description"
        record.license_name = "CC0"
        record.license_names = ["CC0"]
        record.is_private = True
        record.thumbnail_url = "https://example.com/old.png"
        record.detail_metadata = {"keywords": ["lung"]}
        record.detail_status = MetadataStatus.COMPLETE
        record.detail_fetched_at = datetime.now(UTC)
        record.save()

        self.provider.items = [self.item("Updated summary title")]
        self.service.search_and_upsert("kaggle", "lungs")

        record.refresh_from_db()
        self.assertEqual(record.title, "Updated summary title")
        self.assertEqual(record.description, "Existing description")
        self.assertEqual(record.license_names, ["CC0"])
        self.assertTrue(record.is_private)
        self.assertEqual(record.detail_metadata, {"keywords": ["lung"]})

    def test_changed_revision_marks_complete_detail_stale(self):
        record = self.create_from_summary()
        record.detail_status = MetadataStatus.COMPLETE
        record.detail_fetched_at = datetime.now(UTC)
        record.save(update_fields=("detail_status", "detail_fetched_at", "updated_at"))

        self.provider.items = [self.item(remote_version="2")]
        self.service.search_and_upsert("kaggle", "lungs")

        record.refresh_from_db()
        self.assertEqual(record.detail_status, MetadataStatus.STALE)

    def test_unchanged_revision_preserves_complete_status(self):
        record = self.create_from_summary()
        record.detail_status = MetadataStatus.COMPLETE
        record.detail_fetched_at = datetime.now(UTC)
        record.save(update_fields=("detail_status", "detail_fetched_at", "updated_at"))

        self.provider.items = [self.item()]
        self.service.search_and_upsert("kaggle", "lungs")

        record.refresh_from_db()
        self.assertEqual(record.detail_status, MetadataStatus.COMPLETE)

    def test_enrichment_updates_detail_owned_fields(self):
        record = self.create_from_summary()
        self.provider.details = ProviderDatasetDetails(external_id=record.external_id, title="Detailed title",
                                                       subtitle="Clinical cohort", description="Complete description",
                                                       license_names=("CC0", "CC BY 4.0"), is_private=False,
                                                       thumbnail_url="https://example.com/cover.png",
                                                       metadata={"resources": ["patients.csv"]})

        enriched = self.service.fetch_and_enrich(record.pk)

        self.assertEqual(enriched.title, "Detailed title")
        self.assertEqual(enriched.description, "Complete description")
        self.assertEqual(enriched.license_names, ["CC0", "CC BY 4.0"])
        self.assertEqual(enriched.license_name, "CC0")
        self.assertFalse(enriched.is_private)
        self.assertEqual(enriched.detail_status, MetadataStatus.COMPLETE)
        self.assertEqual(enriched.detail_metadata, {"resources": ["patients.csv"]})
        self.assertIsNotNone(enriched.detail_fetched_at)

    def test_omitted_detail_fields_preserve_previous_values(self):
        record = self.create_from_summary()
        record.subtitle = "Existing subtitle"
        record.description = "Existing description"
        record.license_name = "CC0"
        record.license_names = ["CC0"]
        record.is_private = True
        record.thumbnail_url = "https://example.com/old.png"
        record.save()

        self.provider.details = ProviderDatasetDetails(external_id=record.external_id, metadata={"refreshed": True})

        self.service.fetch_and_enrich(record.pk)
        record.refresh_from_db()

        self.assertEqual(record.subtitle, "Existing subtitle")
        self.assertEqual(record.description, "Existing description")
        self.assertEqual(record.license_names, ["CC0"])
        self.assertEqual(record.license_name, "CC0")
        self.assertTrue(record.is_private)
        self.assertEqual(record.thumbnail_url, "https://example.com/old.png")
        self.assertEqual(record.detail_metadata, {"refreshed": True})

    def test_explicit_empty_detail_fields_clear_values(self):
        record = self.create_from_summary()
        record.description = "Existing description"
        record.license_name = "CC0"
        record.license_names = ["CC0"]
        record.save()

        self.provider.details = ProviderDatasetDetails(external_id=record.external_id, description="", license_names=())

        self.service.fetch_and_enrich(record.pk)
        record.refresh_from_db()

        self.assertEqual(record.description, "")
        self.assertEqual(record.license_name, "")
        self.assertEqual(record.license_names, [])

    def test_failed_enrichment_preserves_previous_metadata(self):
        record = self.create_from_summary()
        record.description = "Existing description"
        record.detail_metadata = {"stable": True}
        record.detail_status = MetadataStatus.COMPLETE
        record.save()

        self.provider.detail_error = (ProviderUnavailableError("temporary outage"))
        with self.assertRaises(ProviderUnavailableError):
            self.service.fetch_and_enrich(record.pk)

        record.refresh_from_db()
        self.assertEqual(record.description, "Existing description")
        self.assertEqual(record.detail_metadata, {"stable": True})
        self.assertEqual(record.detail_status, MetadataStatus.FAILED)
        self.assertIn("temporary outage", record.detail_error)

    def test_mismatched_detail_identity_is_rejected(self):
        record = self.create_from_summary()
        self.provider.details = ProviderDatasetDetails(external_id="another/dataset")

        with self.assertRaises(ProviderResponseError):
            self.service.fetch_and_enrich(record.pk)

        record.refresh_from_db()
        self.assertEqual(record.detail_status, MetadataStatus.FAILED)

    def test_revision_changed_during_fetch_marks_detail_stale(self):
        record = self.create_from_summary()
        self.provider.details = ProviderDatasetDetails(external_id=record.external_id, description="Fetched details")

        def change_revision():
            SourceDataset.objects.filter(pk=record.pk).update(remote_version="2")

        self.provider.on_fetch = change_revision

        enriched = self.service.fetch_and_enrich(record.pk)

        self.assertEqual(enriched.detail_status, MetadataStatus.STALE)

    def test_disabled_source_is_rejected(self):
        self.source.is_enabled = False
        self.source.save(update_fields=("is_enabled", "updated_at"))

        with self.assertRaises(CatalogSourceUnavailableError):
            self.service.search_and_upsert("kaggle", "lungs")

        self.assertEqual(self.provider.search_calls, [])

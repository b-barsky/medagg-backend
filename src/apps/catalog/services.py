from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .models import (DataSource, MetadataStatus, SourceDataset)
from .providers import provider_registry
from .providers.dto import (ProviderDatasetDetails, ProviderDatasetSummary)
from .providers.exceptions import (ProviderError, ProviderResponseError)
from .providers.registry import ProviderRegistry


class CatalogSourceUnavailableError(LookupError):
    """A requested source is missing or disabled."""


class CatalogDatasetNotFoundError(LookupError):
    """A requested catalog record does not exist."""


@dataclass(frozen=True, slots=True)
class CatalogSyncResult:
    source_slug: str
    query: str
    page: int

    fetched_count: int
    stored_count: int
    created_count: int
    refreshed_count: int

    record_ids: tuple[int, ...]


class CatalogService:
    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self._registry = (provider_registry if registry is None else registry)

    def search_and_upsert(self, source_slug: str, query: str, *, page: int = 1) -> CatalogSyncResult:
        normalized_source = (source_slug.strip().lower())
        normalized_query = query.strip()

        if not normalized_source:
            raise ValueError("Source slug cannot be blank.")

        if not normalized_query:
            raise ValueError("Search query cannot be blank.")

        if page < 1:
            raise ValueError("Page must be greater than or equal to 1.")

        source = self._get_enabled_source(normalized_source)
        provider = self._registry.create(normalized_source)

        # Never hold a database transaction while waiting on a provider.
        provider_page = provider.search_summary(normalized_query, page=page)

        # Defensively collapse duplicate entries in one provider response.
        # The final occurrence wins.
        unique_items: dict[str, ProviderDatasetSummary] = {}

        for item in provider_page.items:
            external_id = item.external_id.strip()

            if not external_id:
                raise ProviderResponseError("Provider returned a blank external dataset ID.")

            unique_items[external_id] = item

        seen_at = timezone.now()
        stored_records: list[SourceDataset] = []
        created_count = 0

        with transaction.atomic():
            for external_id, item in unique_items.items():
                record, created = self._upsert_summary(source=source, external_id=external_id, item=item,
                                                       seen_at=seen_at)
                stored_records.append(record)
                created_count += int(created)

        stored_count = len(stored_records)

        return CatalogSyncResult(source_slug=normalized_source, query=provider_page.query, page=provider_page.page,
                                 fetched_count=len(provider_page.items), stored_count=stored_count,
                                 created_count=created_count, refreshed_count=stored_count - created_count,
                                 record_ids=tuple(record.pk for record in stored_records))

    def fetch_and_enrich(self, source_dataset_id: int) -> SourceDataset:
        try:
            source_dataset = (SourceDataset.objects.select_related("source").get(pk=source_dataset_id))
        except SourceDataset.DoesNotExist as exc:
            raise CatalogDatasetNotFoundError(f"Source dataset '{source_dataset_id}' does not exist.") from exc

        source = source_dataset.source

        if not source.is_enabled:
            raise CatalogSourceUnavailableError(f"Data source '{source.slug}' is disabled.")

        provider = self._registry.create(source.slug)
        requested_source_version = source_dataset.remote_version
        requested_source_updated_at = source_dataset.remote_updated_at

        try:
            details = provider.fetch_details(source_dataset.external_id)
            self._validate_details_identity(source_dataset, details)
        except ProviderError as exc:
            self._record_detail_failure(source_dataset.pk, exc)
            raise

        fetched_at = timezone.now()

        with transaction.atomic():
            current = (SourceDataset.objects.select_for_update().get(pk=source_dataset.pk))

            self._apply_details(current, details, fetched_at=fetched_at,
                                requested_source_version=requested_source_version,
                                requested_source_updated_at=(requested_source_updated_at))

        return current

    def _get_enabled_source(self, source_slug: str) -> DataSource:
        try:
            source = DataSource.objects.get(slug=source_slug)
        except DataSource.DoesNotExist as exc:
            raise CatalogSourceUnavailableError(f"Data source '{source_slug}' does not exist.") from exc

        if not source.is_enabled:
            raise CatalogSourceUnavailableError(f"Data source '{source_slug}' is disabled.")

        return source

    @classmethod
    def _upsert_summary(cls, *, source: DataSource, external_id: str, item: ProviderDatasetSummary,
                        seen_at: datetime) -> tuple[SourceDataset, bool]:
        existing = (SourceDataset.objects.select_for_update().filter(source=source, external_id=external_id).first())

        previous_version = (existing.remote_version if existing is not None else "")
        previous_updated_at = (existing.remote_updated_at if existing is not None else None)

        record, created = SourceDataset.objects.update_or_create(source=source, external_id=external_id,
                                                                 defaults=cls._summary_defaults(item, seen_at))

        if created:
            return record, True

        if cls._summary_revision_changed(previous_version=previous_version, current_version=item.remote_version,
                                         previous_updated_at=previous_updated_at,
                                         current_updated_at=item.remote_updated_at):
            next_status = (MetadataStatus.STALE if cls._has_successful_details(record) else MetadataStatus.PENDING)
            fields_to_update: list[str] = []

            if record.detail_status != next_status:
                record.detail_status = next_status
                fields_to_update.append("detail_status")

            if record.detail_error:
                record.detail_error = ""
                fields_to_update.append("detail_error")

            if fields_to_update:
                record.save(update_fields=[*fields_to_update, "updated_at"])

        return record, False

    @staticmethod
    def _summary_defaults(item: ProviderDatasetSummary, seen_at: datetime) -> dict[str, object]:
        """
        Fields owned by the provider's list/search response.

        Detail-owned fields are intentionally absent so a later summary
        refresh cannot erase previously enriched metadata.
        """

        return {"source_url": item.source_url, "title": item.title, "owner_name": item.owner_name,
                "owner_ref": item.owner_ref, "total_bytes": item.total_bytes, "download_count": item.download_count,
                "vote_count": item.vote_count, "view_count": item.view_count, "usability_rating": item.usability_rating,
                "remote_version": item.remote_version, "remote_updated_at": item.remote_updated_at,
                "summary_metadata": dict(item.metadata), "last_seen_at": seen_at}

    @staticmethod
    def _summary_revision_changed(*, previous_version: str, current_version: str, previous_updated_at: datetime | None,
                                  current_updated_at: datetime | None) -> bool:
        version_changed = bool(current_version) and (current_version != previous_version)
        timestamp_changed = (current_updated_at is not None and current_updated_at != previous_updated_at)

        return version_changed or timestamp_changed

    @staticmethod
    def _has_successful_details(record: SourceDataset, ) -> bool:
        return (record.detail_fetched_at is not None or bool(record.detail_metadata) or record.detail_status in {
            MetadataStatus.COMPLETE, MetadataStatus.STALE})

    @staticmethod
    def _validate_details_identity(source_dataset: SourceDataset, details: ProviderDatasetDetails, ) -> None:
        if details.external_id.strip() != source_dataset.external_id:
            raise ProviderResponseError("Provider returned details for an unexpected dataset: "
                                        f"'{details.external_id}' != "
                                        f"'{source_dataset.external_id}'.")

    @staticmethod
    def _apply_details(record: SourceDataset, details: ProviderDatasetDetails, *, fetched_at: datetime,
                       requested_source_version: str, requested_source_updated_at: datetime | None, ) -> None:
        update_fields = ["license_name", "license_names", "detail_metadata", "detail_status", "detail_fetched_at",
                         "detail_source_updated_at", "detail_error"]

        if details.title is not None:
            record.title = details.title
            update_fields.append("title")

        if details.subtitle is not None:
            record.subtitle = details.subtitle
            update_fields.append("subtitle")

        if details.description is not None:
            record.description = details.description
            update_fields.append("description")

        if details.is_private is not None:
            record.is_private = details.is_private
            update_fields.append("is_private")

        if details.thumbnail_url is not None:
            record.thumbnail_url = details.thumbnail_url
            update_fields.append("thumbnail_url")

        if details.license_names is not None:
            license_names = list(details.license_names)
            record.license_names = license_names
            record.license_name = (license_names[0] if license_names else "")
            update_fields.extend(("license_name", "license_names"))

        record.detail_metadata = dict(details.metadata)
        record.detail_fetched_at = fetched_at
        record.detail_source_updated_at = (requested_source_updated_at)
        record.detail_error = ""

        version_changed_during_fetch = bool(record.remote_version) and (
                record.remote_version != requested_source_version)
        timestamp_changed_during_fetch = (
                record.remote_updated_at is not None and record.remote_updated_at != requested_source_updated_at)
        changed_during_fetch = (version_changed_during_fetch or timestamp_changed_during_fetch)
        record.detail_status = (MetadataStatus.STALE if changed_during_fetch else MetadataStatus.COMPLETE)

        record.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])

    @staticmethod
    def _record_detail_failure(source_dataset_id: int, error: ProviderError) -> None:
        with transaction.atomic():
            record = (SourceDataset.objects.select_for_update().filter(pk=source_dataset_id).first())

            if record is None:
                return

            record.detail_status = MetadataStatus.FAILED
            record.detail_error = f"{type(error).__name__}: {error}"[:4000]
            record.save(update_fields=["detail_status", "detail_error", "updated_at"])

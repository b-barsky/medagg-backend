from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import (DataSource, MetadataStatus, SourceDataset)
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.services import (CatalogDatasetNotFoundError, CatalogService, CatalogSourceUnavailableError)


class Command(BaseCommand):
    help = ("Fetch complete metadata for pending or stale catalog records.")

    def add_arguments(self, parser) -> None:
        parser.add_argument("source", help="Registered source slug.")
        parser.add_argument("--dataset-id", type=int, help="Enrich one SourceDataset primary key.")
        parser.add_argument("--limit", type=int, default=20, help="Maximum records to enrich (default: 20).")
        parser.add_argument("--retry-failed", action="store_true",
                            help="Also retry records whose last enrichment failed.")

    def handle(self, *args, **options) -> None:
        source_slug = options["source"].strip().lower()
        limit = options["limit"]

        if not source_slug:
            raise CommandError("Source slug cannot be blank.")

        if limit < 1:
            raise CommandError("--limit must be at least 1.")

        try:
            source = DataSource.objects.get(slug=source_slug)
        except DataSource.DoesNotExist as exc:
            raise CommandError(f"Data source '{source_slug}' does not exist.") from exc

        if not source.is_enabled:
            raise CommandError(f"Data source '{source_slug}' is disabled.")

        queryset = SourceDataset.objects.filter(source=source)
        dataset_id = options["dataset_id"]

        if dataset_id is not None:
            queryset = queryset.filter(pk=dataset_id)
        else:
            statuses = [MetadataStatus.PENDING, MetadataStatus.STALE, ]

            if options["retry_failed"]:
                statuses.append(MetadataStatus.FAILED)

            queryset = queryset.filter(detail_status__in=statuses)

        record_ids = list(queryset.order_by("last_seen_at", "pk").values_list("pk", flat=True)[:limit])

        if dataset_id is not None and not record_ids:
            raise CommandError(f"Source dataset '{dataset_id}' does not belong to "
                               f"source '{source_slug}'.")

        service = CatalogService()
        succeeded = 0
        failed = 0

        for record_id in record_ids:
            try:
                record = service.fetch_and_enrich(record_id)
            except (CatalogDatasetNotFoundError, CatalogSourceUnavailableError, ProviderError) as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"source_dataset={record_id}: {exc}"))
                continue

            succeeded += 1
            self.stdout.write(self.style.SUCCESS(f"source_dataset={record.pk}: "
                                                 f"status={record.detail_status}"))

        self.stdout.write(f"source={source_slug}, selected={len(record_ids)}, "
                          f"successful={succeeded}, failed={failed}")

        if failed:
            raise CommandError(f"Failed to enrich {failed} catalog record(s).")

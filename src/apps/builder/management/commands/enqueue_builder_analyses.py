from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.datasets.models import DatasetVersion, DatasetVersionStatus

from apps.builder.models import AnalysisStatus
from apps.builder.tasks import enqueue_analysis_for_version


class Command(BaseCommand):
    help = "Queue schema/semantic analysis for available dataset versions."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--dataset-id", type=int)
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Also retry failed and unsupported analyses.",
        )

    def handle(self, *args, **options) -> None:
        retry_statuses = [AnalysisStatus.PENDING]
        if options["retry_failed"]:
            retry_statuses.extend(
                [AnalysisStatus.FAILED, AnalysisStatus.UNSUPPORTED]
            )
        queryset = DatasetVersion.objects.filter(
            Q(builder_analysis__isnull=True)
            | Q(builder_analysis__status__in=retry_statuses),
            status=DatasetVersionStatus.AVAILABLE,
        )
        if options["dataset_id"]:
            queryset = queryset.filter(dataset_id=options["dataset_id"])
        queryset = queryset.order_by("created_at")
        if options["limit"] > 0:
            queryset = queryset[: options["limit"]]
        count = 0
        for version in queryset:
            analysis = enqueue_analysis_for_version(version.pk)
            if analysis.status == AnalysisStatus.QUEUED:
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Queued {count} dataset analyses."))

import uuid

from django.db import models

from apps.catalog.models import DataSource, SourceDataset


class SearchRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"


class SearchProviderStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    RETRYING = "retrying", "Retrying"
    ENRICHING = "enriching", "Enriching"
    SUCCEEDED = "succeeded", "Succeeded"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"


class SearchResultEnrichmentStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    RETRYING = "retrying", "Retrying"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class SearchRun(models.Model):
    """A durable, user-visible federated dataset search request."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    query = models.CharField(max_length=100)
    status = models.CharField(
        max_length=16,
        choices=SearchRunStatus.choices,
        default=SearchRunStatus.QUEUED,
    )
    deadline_at = models.DateTimeField()

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("status", "deadline_at"),
                name="search_run_state_deadline_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            SearchRunStatus.COMPLETED,
            SearchRunStatus.PARTIAL,
            SearchRunStatus.FAILED,
        }

    def __str__(self) -> str:
        return f"{self.id}: {self.query}"


class SearchProviderRun(models.Model):
    """Execution state for one source within a SearchRun."""

    search_run = models.ForeignKey(
        SearchRun,
        on_delete=models.CASCADE,
        related_name="provider_runs",
    )
    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="search_provider_runs",
    )

    position = models.PositiveSmallIntegerField()
    provider_page = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16,
        choices=SearchProviderStatus.choices,
        default=SearchProviderStatus.QUEUED,
    )
    task_id = models.UUIDField(
        null=True,
        blank=True,
    )

    attempt_count = models.PositiveSmallIntegerField(default=0)
    result_count = models.PositiveIntegerField(default=0)
    detail_completed_count = models.PositiveIntegerField(default=0)
    detail_failed_count = models.PositiveIntegerField(default=0)

    error_code = models.CharField(
        max_length=64,
        blank=True,
    )
    error_message = models.TextField(blank=True)

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("search_run", "source"),
                name="search_pr_run_source_uniq",
            ),
            models.UniqueConstraint(
                fields=("search_run", "position"),
                name="search_pr_run_position_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status",),
                name="search_provider_state_idx",
            ),
        ]

    @property
    def detail_pending_count(self) -> int:
        return max(
            self.result_count
            - self.detail_completed_count
            - self.detail_failed_count,
            0,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            SearchProviderStatus.SUCCEEDED,
            SearchProviderStatus.PARTIAL,
            SearchProviderStatus.FAILED,
        }

    def __str__(self) -> str:
        return (
            f"{self.search_run_id}:"
            f"{self.source.slug}:"
            f"{self.status}"
        )


class SearchResult(models.Model):
    """One catalog result returned by one provider execution."""

    search_run = models.ForeignKey(
        SearchRun,
        on_delete=models.CASCADE,
        related_name="search_results",
    )
    provider_run = models.ForeignKey(
        SearchProviderRun,
        on_delete=models.CASCADE,
        related_name="search_results",
    )
    source_dataset = models.ForeignKey(
        SourceDataset,
        on_delete=models.CASCADE,
        related_name="search_results",
    )
    rank = models.PositiveIntegerField()

    enrichment_status = models.CharField(
        max_length=16,
        choices=SearchResultEnrichmentStatus.choices,
        default=SearchResultEnrichmentStatus.QUEUED,
    )
    enrichment_task_id = models.UUIDField(
        null=True,
        blank=True,
    )
    enrichment_attempt_count = models.PositiveSmallIntegerField(
        default=0,
    )
    enrichment_error_code = models.CharField(
        max_length=64,
        blank=True,
    )
    enrichment_error_message = models.TextField(blank=True)
    enrichment_started_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    enrichment_last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    enrichment_finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = (
            "provider_run__position",
            "rank",
            "id",
        )
        constraints = [
            models.UniqueConstraint(
                fields=("search_run", "source_dataset"),
                name="search_result_run_dataset_uniq",
            ),
            models.UniqueConstraint(
                fields=("provider_run", "rank"),
                name="search_result_provider_rank_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("search_run", "rank"),
                name="search_result_order_idx",
            ),
            models.Index(
                fields=("enrichment_status",),
                name="search_result_enrich_idx",
            ),
        ]

    @property
    def is_enrichment_terminal(self) -> bool:
        return self.enrichment_status in {
            SearchResultEnrichmentStatus.SUCCEEDED,
            SearchResultEnrichmentStatus.FAILED,
        }

    def __str__(self) -> str:
        return (
            f"{self.search_run_id}:"
            f"{self.source_dataset_id}:"
            f"{self.rank}"
        )

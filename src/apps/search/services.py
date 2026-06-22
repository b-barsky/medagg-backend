import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from django.conf import settings
from django.db import transaction
from django.db.models import F, Prefetch, QuerySet
from django.utils import timezone
from kombu.exceptions import OperationalError

from apps.catalog.models import (
    DataSource,
    MetadataStatus,
    SourceDataset,
)
from apps.catalog.providers import provider_registry
from apps.catalog.providers.registry import ProviderRegistry

from .models import (
    SearchProviderRun,
    SearchProviderStatus,
    SearchResult,
    SearchResultEnrichmentStatus,
    SearchRun,
    SearchRunStatus,
)


logger = logging.getLogger(__name__)

BROKER_ERRORS = (
    OperationalError,
    ConnectionError,
    TimeoutError,
    OSError,
)


class SearchSourceValidationError(ValueError):
    """The requested source set cannot be used for a search run."""

    def __init__(
        self,
        message: str,
        *,
        unavailable_sources: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.unavailable_sources = unavailable_sources


class SearchResultPersistenceError(RuntimeError):
    """Provider results could not be attached safely to a search run."""


@dataclass(frozen=True, slots=True)
class ProviderExecutionContext:
    provider_run_id: int
    search_run_id: UUID
    source_slug: str
    query: str
    provider_page: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ResultEnrichmentContext:
    search_result_id: int
    provider_run_id: int
    search_run_id: UUID
    source_slug: str
    source_dataset_id: int
    external_id: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class EnrichmentDispatchResult:
    queued_count: int
    publish_failed_count: int


class SearchRunService:
    def __init__(
        self,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._registry = (
            provider_registry
            if registry is None
            else registry
        )

    def create_run(
        self,
        query: str,
        *,
        source_slugs: list[str] | tuple[str, ...] | None = None,
        provider_page: int = 1,
    ) -> SearchRun:
        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("Search query cannot be blank.")

        if provider_page < 1:
            raise ValueError(
                "Provider page must be greater than or equal to 1."
            )

        sources = self._resolve_sources(source_slugs)
        now = timezone.now()

        with transaction.atomic():
            search_run = SearchRun.objects.create(
                query=normalized_query,
                status=SearchRunStatus.QUEUED,
                deadline_at=(
                    now
                    + timedelta(
                        seconds=settings.SEARCH_RUN_TIMEOUT_SECONDS
                    )
                ),
            )

            SearchProviderRun.objects.bulk_create(
                [
                    SearchProviderRun(
                        search_run=search_run,
                        source=source,
                        position=position,
                        provider_page=provider_page,
                        status=SearchProviderStatus.QUEUED,
                        task_id=uuid4(),
                    )
                    for position, source in enumerate(
                        sources,
                        start=1,
                    )
                ]
            )

        logger.info(
            "Created durable search run.",
            extra={
                "event": "search.run.created",
                "search_run_id": str(search_run.pk),
                "provider_count": len(sources),
                "query_length": len(normalized_query),
                "deadline_at": search_run.deadline_at.isoformat(),
            },
        )

        return self.get_run(search_run.pk)

    def enqueue_run(
        self,
        search_run_id: UUID | str,
    ) -> None:
        """
        Publish one summary-search task per provider.

        Broker publication failures are persisted as provider failures so the
        HTTP request can still return a durable SearchRun identifier.
        """

        from .tasks import expire_search_run, search_provider

        search_run = SearchRun.objects.get(pk=search_run_id)
        provider_runs = list(
            SearchProviderRun.objects
            .select_related("source")
            .filter(
                search_run=search_run,
                status=SearchProviderStatus.QUEUED,
            )
            .order_by("position", "id")
        )

        self._enqueue_deadline_task(
            search_run,
            expire_search_run=expire_search_run,
        )

        for provider_run in provider_runs:
            if provider_run.task_id is None:
                provider_run.task_id = uuid4()
                provider_run.save(
                    update_fields=("task_id", "updated_at")
                )

            try:
                search_provider.apply_async(
                    args=(provider_run.pk,),
                    task_id=str(provider_run.task_id),
                    queue=settings.SEARCH_TASK_QUEUE,
                )
            except BROKER_ERRORS:
                self.fail_provider(
                    provider_run.pk,
                    error_code="broker_publish_failed",
                    error_message=(
                        "The provider summary task could not be queued."
                    ),
                )
                logger.exception(
                    "Failed to publish provider search task.",
                    extra={
                        "event": "search.provider.publish_failed",
                        "search_run_id": str(search_run_id),
                        "provider_run_id": provider_run.pk,
                        "source_slug": provider_run.source.slug,
                        "task_id": str(provider_run.task_id),
                        "queue": settings.SEARCH_TASK_QUEUE,
                    },
                )
                continue

            logger.info(
                "Published provider search task.",
                extra={
                    "event": "search.provider.published",
                    "search_run_id": str(search_run_id),
                    "provider_run_id": provider_run.pk,
                    "source_slug": provider_run.source.slug,
                    "task_id": str(provider_run.task_id),
                    "queue": settings.SEARCH_TASK_QUEUE,
                },
            )

    def enqueue_result_enrichments(
        self,
        provider_run_id: int,
    ) -> EnrichmentDispatchResult:
        """Publish one detail-metadata task for every queued result."""

        from .tasks import enrich_search_result

        queued_results = list(
            SearchResult.objects
            .select_related(
                "provider_run__source",
                "search_run",
                "source_dataset",
            )
            .filter(
                provider_run_id=provider_run_id,
                enrichment_status=(
                    SearchResultEnrichmentStatus.QUEUED
                ),
            )
            .order_by("rank", "id")
        )

        queued_count = 0
        publish_failed_count = 0

        for result in queued_results:
            if result.enrichment_task_id is None:
                result.enrichment_task_id = uuid4()
                result.save(
                    update_fields=("enrichment_task_id",)
                )

            try:
                enrich_search_result.apply_async(
                    args=(result.pk,),
                    task_id=str(result.enrichment_task_id),
                    queue=settings.SEARCH_DETAIL_TASK_QUEUE,
                )
            except BROKER_ERRORS:
                publish_failed_count += 1
                self.fail_result_enrichment(
                    result.pk,
                    error_code="broker_publish_failed",
                    error_message=(
                        "The detail-metadata task could not be queued."
                    ),
                )
                logger.exception(
                    "Failed to publish detail enrichment task.",
                    extra={
                        "event": "search.result.publish_failed",
                        "search_run_id": str(result.search_run_id),
                        "provider_run_id": result.provider_run_id,
                        "search_result_id": result.pk,
                        "source_dataset_id": result.source_dataset_id,
                        "source_slug": result.provider_run.source.slug,
                        "task_id": str(result.enrichment_task_id),
                        "queue": settings.SEARCH_DETAIL_TASK_QUEUE,
                    },
                )
                continue

            queued_count += 1
            logger.info(
                "Published detail enrichment task.",
                extra={
                    "event": "search.result.published",
                    "search_run_id": str(result.search_run_id),
                    "provider_run_id": result.provider_run_id,
                    "search_result_id": result.pk,
                    "source_dataset_id": result.source_dataset_id,
                    "source_slug": result.provider_run.source.slug,
                    "task_id": str(result.enrichment_task_id),
                    "queue": settings.SEARCH_DETAIL_TASK_QUEUE,
                },
            )

        return EnrichmentDispatchResult(
            queued_count=queued_count,
            publish_failed_count=publish_failed_count,
        )

    @staticmethod
    def _enqueue_deadline_task(
        search_run: SearchRun,
        *,
        expire_search_run,
    ) -> None:
        """Schedule an idempotent overall-deadline reconciliation task."""

        deadline_task_id = uuid5(
            NAMESPACE_URL,
            f"medagg:search-run:{search_run.pk}:expire",
        )
        countdown = max(
            1.0,
            (search_run.deadline_at - timezone.now()).total_seconds()
            + 1.0,
        )

        try:
            expire_search_run.apply_async(
                args=(str(search_run.pk),),
                task_id=str(deadline_task_id),
                countdown=countdown,
                queue=settings.SEARCH_TASK_QUEUE,
            )
        except BROKER_ERRORS:
            # Retrieval also reconciles overdue runs. Failure to publish this
            # safety-net task must not discard an otherwise valid SearchRun.
            logger.exception(
                "Failed to publish the search deadline task.",
                extra={
                    "event": "search.run.deadline_publish_failed",
                    "search_run_id": str(search_run.pk),
                    "task_id": str(deadline_task_id),
                    "queue": settings.SEARCH_TASK_QUEUE,
                },
            )
        else:
            logger.info(
                "Published search deadline task.",
                extra={
                    "event": "search.run.deadline_published",
                    "search_run_id": str(search_run.pk),
                    "task_id": str(deadline_task_id),
                    "deadline_at": search_run.deadline_at.isoformat(),
                    "queue": settings.SEARCH_TASK_QUEUE,
                },
            )

    def get_run(
        self,
        search_run_id: UUID | str,
    ) -> SearchRun:
        provider_queryset = (
            SearchProviderRun.objects
            .select_related("source")
            .order_by("position", "id")
        )

        return (
            SearchRun.objects
            .prefetch_related(
                Prefetch(
                    "provider_runs",
                    queryset=provider_queryset,
                )
            )
            .get(pk=search_run_id)
        )

    @staticmethod
    def results_queryset(
        search_run_id: UUID | str,
    ) -> QuerySet[SearchResult]:
        return (
            SearchResult.objects
            .filter(search_run_id=search_run_id)
            .select_related(
                "provider_run",
                "provider_run__source",
                "source_dataset",
                "source_dataset__source",
            )
            .order_by(
                "provider_run__position",
                "rank",
                "id",
            )
        )

    def start_provider(
        self,
        provider_run_id: int,
        *,
        task_id: str | None,
    ) -> ProviderExecutionContext | None:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run = self._lock_provider_run(
                provider_run_id,
                include_source=True,
            )

            if provider_run.is_terminal or search_run.is_terminal:
                return None

            if search_run.deadline_at <= now:
                self._fail_provider_locked(
                    provider_run,
                    error_code="search_deadline_exceeded",
                    error_message=(
                        "The search run deadline was reached before this "
                        "provider could finish."
                    ),
                    finished_at=now,
                )
                self._reconcile_run_locked(search_run, now=now)
                return None

            normalized_task_id = self._optional_uuid(task_id)

            # Ignore stale broker messages. Integer provider IDs can be reused
            # after a development database reset while Redis still has an old
            # durable message.
            if provider_run.task_id is not None:
                if normalized_task_id != provider_run.task_id:
                    return None
            elif normalized_task_id is not None:
                provider_run.task_id = normalized_task_id

            provider_run.status = SearchProviderStatus.RUNNING
            provider_run.attempt_count += 1
            provider_run.last_attempt_at = now
            provider_run.finished_at = None
            provider_run.error_code = ""
            provider_run.error_message = ""

            if provider_run.started_at is None:
                provider_run.started_at = now

            provider_run.save(
                update_fields=(
                    "status",
                    "task_id",
                    "attempt_count",
                    "last_attempt_at",
                    "started_at",
                    "finished_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                )
            )

            search_run.status = SearchRunStatus.RUNNING

            if search_run.started_at is None:
                search_run.started_at = now

            search_run.finished_at = None
            search_run.save(
                update_fields=(
                    "status",
                    "started_at",
                    "finished_at",
                    "updated_at",
                )
            )

            return ProviderExecutionContext(
                provider_run_id=provider_run.pk,
                search_run_id=search_run.pk,
                source_slug=provider_run.source.slug,
                query=search_run.query,
                provider_page=provider_run.provider_page,
                attempt_count=provider_run.attempt_count,
            )

    def mark_provider_retrying(
        self,
        provider_run_id: int,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run = self._lock_provider_run(
                provider_run_id
            )

            if provider_run.is_terminal or search_run.is_terminal:
                return False

            if search_run.deadline_at <= now:
                self._fail_provider_locked(
                    provider_run,
                    error_code="search_deadline_exceeded",
                    error_message=(
                        "The search run deadline was reached before a retry "
                        "could be scheduled."
                    ),
                    finished_at=now,
                )
                self._reconcile_run_locked(search_run, now=now)
                return False

            provider_run.status = SearchProviderStatus.RETRYING
            provider_run.error_code = self._truncate(
                error_code,
                64,
            )
            provider_run.error_message = self._truncate(
                error_message,
                4000,
            )
            provider_run.finished_at = None
            provider_run.save(
                update_fields=(
                    "status",
                    "error_code",
                    "error_message",
                    "finished_at",
                    "updated_at",
                )
            )

            self._reconcile_run_locked(
                search_run,
                now=now,
            )

        return True

    def publish_provider_results(
        self,
        provider_run_id: int,
        *,
        source_dataset_ids: tuple[int, ...] | list[int],
    ) -> bool:
        """
        Persist summary results immediately and prepare detail tasks.

        Existing SearchResult rows are preserved where possible so a redelivered
        summary task cannot invalidate detail tasks that were already published.
        """

        unique_ids = tuple(dict.fromkeys(source_dataset_ids))
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run = self._lock_provider_run(
                provider_run_id,
                include_source=True,
            )

            if provider_run.is_terminal or search_run.is_terminal:
                return False

            if search_run.deadline_at <= now:
                self._fail_provider_locked(
                    provider_run,
                    error_code="search_deadline_exceeded",
                    error_message=(
                        "The provider result arrived after the search run "
                        "deadline."
                    ),
                    finished_at=now,
                )
                self._reconcile_run_locked(search_run, now=now)
                return False

            datasets = self._validated_datasets_for_provider(
                provider_run,
                unique_ids,
            )
            datasets_by_id = {
                dataset.pk: dataset
                for dataset in datasets
            }

            existing_results = list(
                SearchResult.objects
                .select_for_update()
                .filter(provider_run=provider_run)
                .order_by("id")
            )
            existing_by_dataset_id = {
                result.source_dataset_id: result
                for result in existing_results
            }

            # Avoid temporary rank collisions when provider ordering changes.
            if existing_results:
                SearchResult.objects.filter(
                    provider_run=provider_run
                ).update(rank=F("rank") + 1_000_000)

            retained_result_ids: set[int] = set()

            for rank, source_dataset_id in enumerate(
                unique_ids,
                start=1,
            ):
                dataset = datasets_by_id[source_dataset_id]
                result = existing_by_dataset_id.get(source_dataset_id)

                if result is None:
                    result = SearchResult(
                        search_run=search_run,
                        provider_run=provider_run,
                        source_dataset=dataset,
                        rank=rank,
                    )
                    self._initialize_result_enrichment(
                        result,
                        dataset=dataset,
                        now=now,
                    )
                    result.save()
                else:
                    result.rank = rank
                    result.search_run = search_run

                    if dataset.detail_status == MetadataStatus.COMPLETE:
                        self._mark_result_succeeded_fields(
                            result,
                            finished_at=now,
                        )
                    elif (
                        result.enrichment_status
                        == SearchResultEnrichmentStatus.SUCCEEDED
                    ):
                        self._reset_result_for_enrichment(result)

                    result.save(
                        update_fields=(
                            "search_run",
                            "rank",
                            "enrichment_status",
                            "enrichment_task_id",
                            "enrichment_attempt_count",
                            "enrichment_error_code",
                            "enrichment_error_message",
                            "enrichment_started_at",
                            "enrichment_last_attempt_at",
                            "enrichment_finished_at",
                        )
                    )

                retained_result_ids.add(result.pk)

            SearchResult.objects.filter(
                provider_run=provider_run
            ).exclude(pk__in=retained_result_ids).delete()

            self._reconcile_provider_locked(
                provider_run,
                now=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

        return True

    def complete_provider(
        self,
        provider_run_id: int,
        *,
        source_dataset_ids: tuple[int, ...] | list[int],
    ) -> bool:
        """Compatibility helper used by tests and non-enriching providers."""

        published = self.publish_provider_results(
            provider_run_id,
            source_dataset_ids=source_dataset_ids,
        )

        if not published:
            return False

        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run = self._lock_provider_run(
                provider_run_id
            )

            if provider_run.is_terminal:
                return True

            results = list(
                SearchResult.objects
                .select_for_update()
                .filter(provider_run=provider_run)
            )

            for result in results:
                self._mark_result_succeeded_fields(
                    result,
                    finished_at=now,
                )
                result.save(
                    update_fields=(
                        "enrichment_status",
                        "enrichment_task_id",
                        "enrichment_error_code",
                        "enrichment_error_message",
                        "enrichment_finished_at",
                    )
                )

            self._reconcile_provider_locked(
                provider_run,
                now=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

        return True

    def start_result_enrichment(
        self,
        search_result_id: int,
        *,
        task_id: str | None,
    ) -> ResultEnrichmentContext | None:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run, result = (
                self._lock_search_result(
                    search_result_id,
                    include_related=True,
                )
            )

            if result.is_enrichment_terminal or search_run.is_terminal:
                return None

            if search_run.deadline_at <= now:
                self._fail_result_locked(
                    result,
                    error_code="search_deadline_exceeded",
                    error_message=(
                        "The overall search deadline was reached before "
                        "metadata enrichment could finish."
                    ),
                    finished_at=now,
                )
                self._reconcile_provider_locked(
                    provider_run,
                    now=now,
                )
                self._reconcile_run_locked(
                    search_run,
                    now=now,
                )
                return None

            normalized_task_id = self._optional_uuid(task_id)

            if result.enrichment_task_id is not None:
                if normalized_task_id != result.enrichment_task_id:
                    return None
            elif normalized_task_id is not None:
                result.enrichment_task_id = normalized_task_id

            result.enrichment_status = (
                SearchResultEnrichmentStatus.RUNNING
            )
            result.enrichment_attempt_count += 1
            result.enrichment_last_attempt_at = now
            result.enrichment_finished_at = None
            result.enrichment_error_code = ""
            result.enrichment_error_message = ""

            if result.enrichment_started_at is None:
                result.enrichment_started_at = now

            result.save(
                update_fields=(
                    "enrichment_status",
                    "enrichment_task_id",
                    "enrichment_attempt_count",
                    "enrichment_started_at",
                    "enrichment_last_attempt_at",
                    "enrichment_finished_at",
                    "enrichment_error_code",
                    "enrichment_error_message",
                )
            )

            self._mark_provider_enriching_locked(
                provider_run,
                now=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

            return ResultEnrichmentContext(
                search_result_id=result.pk,
                provider_run_id=provider_run.pk,
                search_run_id=search_run.pk,
                source_slug=provider_run.source.slug,
                source_dataset_id=result.source_dataset_id,
                external_id=result.source_dataset.external_id,
                attempt_count=result.enrichment_attempt_count,
            )

    def mark_result_retrying(
        self,
        search_result_id: int,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run, result = (
                self._lock_search_result(search_result_id)
            )

            if result.is_enrichment_terminal or search_run.is_terminal:
                return False

            if search_run.deadline_at <= now:
                self._fail_result_locked(
                    result,
                    error_code="search_deadline_exceeded",
                    error_message=(
                        "The overall search deadline was reached before a "
                        "metadata retry could be scheduled."
                    ),
                    finished_at=now,
                )
            else:
                result.enrichment_status = (
                    SearchResultEnrichmentStatus.RETRYING
                )
                result.enrichment_error_code = self._truncate(
                    error_code,
                    64,
                )
                result.enrichment_error_message = self._truncate(
                    error_message,
                    4000,
                )
                result.enrichment_finished_at = None
                result.save(
                    update_fields=(
                        "enrichment_status",
                        "enrichment_error_code",
                        "enrichment_error_message",
                        "enrichment_finished_at",
                    )
                )
                self._mark_provider_enriching_locked(
                    provider_run,
                    now=now,
                )

            self._reconcile_provider_locked(
                provider_run,
                now=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

            return (
                result.enrichment_status
                == SearchResultEnrichmentStatus.RETRYING
            )

    def complete_result_enrichment(
        self,
        search_result_id: int,
    ) -> bool:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run, result = (
                self._lock_search_result(search_result_id)
            )

            if result.is_enrichment_terminal or search_run.is_terminal:
                return False

            source_dataset = (
                SourceDataset.objects
                .select_for_update()
                .get(pk=result.source_dataset_id)
            )

            if source_dataset.detail_status != MetadataStatus.COMPLETE:
                raise SearchResultPersistenceError(
                    "The provider detail task finished without producing "
                    "complete metadata."
                )

            self._mark_result_succeeded_fields(
                result,
                finished_at=now,
            )
            result.save(
                update_fields=(
                    "enrichment_status",
                    "enrichment_task_id",
                    "enrichment_error_code",
                    "enrichment_error_message",
                    "enrichment_finished_at",
                )
            )

            self._reconcile_provider_locked(
                provider_run,
                now=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

        return True

    def fail_result_enrichment(
        self,
        search_result_id: int,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run, result = (
                self._lock_search_result(search_result_id)
            )

            if result.is_enrichment_terminal or search_run.is_terminal:
                return False

            self._fail_result_locked(
                result,
                error_code=error_code,
                error_message=error_message,
                finished_at=now,
            )
            self._reconcile_provider_locked(
                provider_run,
                now=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

        return True

    def fail_provider(
        self,
        provider_run_id: int,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        now = timezone.now()

        with transaction.atomic():
            search_run, provider_run = self._lock_provider_run(
                provider_run_id
            )

            if provider_run.is_terminal or search_run.is_terminal:
                return False

            self._fail_provider_locked(
                provider_run,
                error_code=error_code,
                error_message=error_message,
                finished_at=now,
            )
            self._reconcile_run_locked(
                search_run,
                now=now,
            )

        return True

    def expire_run_if_needed(
        self,
        search_run_id: UUID | str,
    ) -> bool:
        """
        Fail stalled dispatches and reconcile the overall deadline.

        This method is called by polling and by the delayed Celery watchdog.
        """

        now = timezone.now()
        changed = False
        expired = False

        with transaction.atomic():
            search_run = (
                SearchRun.objects
                .select_for_update()
                .get(pk=search_run_id)
            )

            if search_run.is_terminal:
                return False

            provider_runs = list(
                SearchProviderRun.objects
                .select_for_update()
                .filter(search_run=search_run)
                .order_by("id")
            )

            start_cutoff = now - timedelta(
                seconds=(
                    settings.SEARCH_PROVIDER_START_TIMEOUT_SECONDS
                )
            )

            for provider_run in provider_runs:
                if (
                    provider_run.status == SearchProviderStatus.QUEUED
                    and provider_run.created_at <= start_cutoff
                ):
                    self._fail_provider_locked(
                        provider_run,
                        error_code="worker_not_started",
                        error_message=(
                            "The provider task was queued but no worker "
                            "started it. Check the Celery worker and queues."
                        ),
                        finished_at=now,
                    )
                    changed = True

            if search_run.deadline_at <= now:
                expired = True

                for provider_run in provider_runs:
                    provider_run.refresh_from_db()

                    if provider_run.is_terminal:
                        continue

                    active_results = list(
                        SearchResult.objects
                        .select_for_update()
                        .filter(provider_run=provider_run)
                        .exclude(
                            enrichment_status__in=(
                                SearchResultEnrichmentStatus.SUCCEEDED,
                                SearchResultEnrichmentStatus.FAILED,
                            )
                        )
                    )

                    for result in active_results:
                        self._fail_result_locked(
                            result,
                            error_code="search_deadline_exceeded",
                            error_message=(
                                "The overall search deadline expired before "
                                "metadata enrichment completed."
                            ),
                            finished_at=now,
                        )

                    if provider_run.search_results.exists():
                        self._reconcile_provider_locked(
                            provider_run,
                            now=now,
                        )
                    else:
                        self._fail_provider_locked(
                            provider_run,
                            error_code="search_deadline_exceeded",
                            error_message=(
                                "The search run exceeded its overall deadline."
                            ),
                            finished_at=now,
                        )

                    changed = True

            if changed:
                self._reconcile_run_locked(
                    search_run,
                    now=now,
                )

        if changed:
            logger.warning(
                "Reconciled a stalled or expired search run.",
                extra={
                    "event": "search.run.reconciled_timeout",
                    "search_run_id": str(search_run_id),
                    "deadline_expired": expired,
                },
            )

        return changed

    def _resolve_sources(
        self,
        source_slugs: list[str] | tuple[str, ...] | None,
    ) -> list[DataSource]:
        registered_slugs = set(
            self._registry.registered_slugs()
        )
        enabled_sources = list(
            DataSource.objects
            .filter(
                is_enabled=True,
                slug__in=registered_slugs,
            )
            .order_by("name", "slug")
        )
        enabled_by_slug = {
            source.slug: source
            for source in enabled_sources
        }

        if source_slugs is None:
            if not enabled_sources:
                raise SearchSourceValidationError(
                    "No enabled and registered dataset sources are available."
                )

            return enabled_sources

        normalized_slugs = tuple(
            dict.fromkeys(
                slug.strip().lower()
                for slug in source_slugs
                if slug.strip()
            )
        )

        if not normalized_slugs:
            raise SearchSourceValidationError(
                "At least one dataset source must be selected."
            )

        unavailable = tuple(
            slug
            for slug in normalized_slugs
            if slug not in enabled_by_slug
        )

        if unavailable:
            raise SearchSourceValidationError(
                "Some requested dataset sources are unavailable: "
                + ", ".join(unavailable),
                unavailable_sources=unavailable,
            )

        return [
            enabled_by_slug[slug]
            for slug in normalized_slugs
        ]

    @staticmethod
    def _validated_datasets_for_provider(
        provider_run: SearchProviderRun,
        source_dataset_ids: tuple[int, ...],
    ) -> list[SourceDataset]:
        datasets = list(
            SourceDataset.objects
            .filter(pk__in=source_dataset_ids)
            .order_by("id")
        )
        datasets_by_id = {
            dataset.pk: dataset
            for dataset in datasets
        }

        missing_ids = [
            source_dataset_id
            for source_dataset_id in source_dataset_ids
            if source_dataset_id not in datasets_by_id
        ]

        if missing_ids:
            raise SearchResultPersistenceError(
                "Catalog records disappeared before results could be stored: "
                f"{missing_ids}."
            )

        foreign_ids = [
            dataset.pk
            for dataset in datasets
            if dataset.source_id != provider_run.source_id
        ]

        if foreign_ids:
            raise SearchResultPersistenceError(
                "Provider returned catalog records owned by another source: "
                f"{foreign_ids}."
            )

        return datasets

    @staticmethod
    def _initialize_result_enrichment(
        result: SearchResult,
        *,
        dataset: SourceDataset,
        now: datetime,
    ) -> None:
        if dataset.detail_status == MetadataStatus.COMPLETE:
            SearchRunService._mark_result_succeeded_fields(
                result,
                finished_at=now,
            )
        else:
            SearchRunService._reset_result_for_enrichment(result)

    @staticmethod
    def _reset_result_for_enrichment(
        result: SearchResult,
    ) -> None:
        result.enrichment_status = (
            SearchResultEnrichmentStatus.QUEUED
        )
        result.enrichment_task_id = uuid4()
        result.enrichment_attempt_count = 0
        result.enrichment_error_code = ""
        result.enrichment_error_message = ""
        result.enrichment_started_at = None
        result.enrichment_last_attempt_at = None
        result.enrichment_finished_at = None

    @staticmethod
    def _mark_result_succeeded_fields(
        result: SearchResult,
        *,
        finished_at: datetime,
    ) -> None:
        result.enrichment_status = (
            SearchResultEnrichmentStatus.SUCCEEDED
        )
        result.enrichment_task_id = None
        result.enrichment_error_code = ""
        result.enrichment_error_message = ""
        result.enrichment_finished_at = finished_at

    @staticmethod
    def _mark_provider_enriching_locked(
        provider_run: SearchProviderRun,
        *,
        now: datetime,
    ) -> None:
        provider_run.status = SearchProviderStatus.ENRICHING
        provider_run.finished_at = None

        if provider_run.started_at is None:
            provider_run.started_at = now

        provider_run.save(
            update_fields=(
                "status",
                "started_at",
                "finished_at",
                "updated_at",
            )
        )

    @staticmethod
    def _fail_result_locked(
        result: SearchResult,
        *,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> None:
        result.enrichment_status = (
            SearchResultEnrichmentStatus.FAILED
        )
        result.enrichment_error_code = SearchRunService._truncate(
            error_code,
            64,
        )
        result.enrichment_error_message = SearchRunService._truncate(
            error_message,
            4000,
        )
        result.enrichment_finished_at = finished_at
        result.save(
            update_fields=(
                "enrichment_status",
                "enrichment_error_code",
                "enrichment_error_message",
                "enrichment_finished_at",
            )
        )

    @staticmethod
    def _fail_provider_locked(
        provider_run: SearchProviderRun,
        *,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> None:
        provider_run.status = SearchProviderStatus.FAILED
        provider_run.error_code = SearchRunService._truncate(
            error_code,
            64,
        )
        provider_run.error_message = SearchRunService._truncate(
            error_message,
            4000,
        )
        provider_run.finished_at = finished_at
        provider_run.save(
            update_fields=(
                "status",
                "error_code",
                "error_message",
                "finished_at",
                "updated_at",
            )
        )

    @staticmethod
    def _reconcile_provider_locked(
        provider_run: SearchProviderRun,
        *,
        now: datetime,
    ) -> None:
        statuses = list(
            provider_run.search_results.values_list(
                "enrichment_status",
                flat=True,
            )
        )
        result_count = len(statuses)
        completed_count = sum(
            status == SearchResultEnrichmentStatus.SUCCEEDED
            for status in statuses
        )
        failed_count = sum(
            status == SearchResultEnrichmentStatus.FAILED
            for status in statuses
        )
        active_count = (
            result_count - completed_count - failed_count
        )

        provider_run.result_count = result_count
        provider_run.detail_completed_count = completed_count
        provider_run.detail_failed_count = failed_count

        if result_count == 0:
            next_status = SearchProviderStatus.SUCCEEDED
        elif active_count > 0:
            next_status = SearchProviderStatus.ENRICHING
        elif completed_count == result_count:
            next_status = SearchProviderStatus.SUCCEEDED
        else:
            next_status = SearchProviderStatus.PARTIAL

        provider_run.status = next_status

        if next_status == SearchProviderStatus.PARTIAL:
            provider_run.error_code = "detail_enrichment_partial"
            provider_run.error_message = (
                f"Detailed metadata failed for {failed_count} of "
                f"{result_count} result(s). Summary metadata remains "
                "available."
            )
        elif next_status in {
            SearchProviderStatus.SUCCEEDED,
            SearchProviderStatus.ENRICHING,
        }:
            provider_run.error_code = ""
            provider_run.error_message = ""

        if next_status in {
            SearchProviderStatus.SUCCEEDED,
            SearchProviderStatus.PARTIAL,
        }:
            provider_run.finished_at = now
        else:
            provider_run.finished_at = None

        if provider_run.started_at is None:
            provider_run.started_at = now

        provider_run.save(
            update_fields=(
                "status",
                "result_count",
                "detail_completed_count",
                "detail_failed_count",
                "error_code",
                "error_message",
                "started_at",
                "finished_at",
                "updated_at",
            )
        )

    @staticmethod
    def _reconcile_run_locked(
        search_run: SearchRun,
        *,
        now: datetime,
    ) -> None:
        statuses = list(
            search_run.provider_runs.values_list(
                "status",
                flat=True,
            )
        )

        if not statuses:
            next_status = SearchRunStatus.FAILED
        else:
            terminal_statuses = {
                SearchProviderStatus.SUCCEEDED,
                SearchProviderStatus.PARTIAL,
                SearchProviderStatus.FAILED,
            }
            all_terminal = all(
                status in terminal_statuses
                for status in statuses
            )

            if all_terminal:
                if all(
                    status == SearchProviderStatus.SUCCEEDED
                    for status in statuses
                ):
                    next_status = SearchRunStatus.COMPLETED
                elif any(
                    status in {
                        SearchProviderStatus.SUCCEEDED,
                        SearchProviderStatus.PARTIAL,
                    }
                    for status in statuses
                ):
                    next_status = SearchRunStatus.PARTIAL
                else:
                    next_status = SearchRunStatus.FAILED
            elif all(
                status == SearchProviderStatus.QUEUED
                for status in statuses
            ):
                next_status = SearchRunStatus.QUEUED
            else:
                next_status = SearchRunStatus.RUNNING

        search_run.status = next_status

        if next_status != SearchRunStatus.QUEUED:
            if search_run.started_at is None:
                search_run.started_at = now

        if next_status in {
            SearchRunStatus.COMPLETED,
            SearchRunStatus.PARTIAL,
            SearchRunStatus.FAILED,
        }:
            search_run.finished_at = now
        else:
            search_run.finished_at = None

        search_run.save(
            update_fields=(
                "status",
                "started_at",
                "finished_at",
                "updated_at",
            )
        )

    @staticmethod
    def _lock_provider_run(
        provider_run_id: int,
        *,
        include_source: bool = False,
    ) -> tuple[SearchRun, SearchProviderRun]:
        """Lock parent SearchRun before its SearchProviderRun."""

        search_run_id = (
            SearchProviderRun.objects
            .filter(pk=provider_run_id)
            .values_list("search_run_id", flat=True)
            .get()
        )
        search_run = (
            SearchRun.objects
            .select_for_update()
            .get(pk=search_run_id)
        )
        provider_queryset = (
            SearchProviderRun.objects
            .select_for_update()
        )

        if include_source:
            provider_queryset = provider_queryset.select_related(
                "source"
            )

        provider_run = provider_queryset.get(
            pk=provider_run_id,
            search_run=search_run,
        )
        return search_run, provider_run

    @staticmethod
    def _lock_search_result(
        search_result_id: int,
        *,
        include_related: bool = False,
    ) -> tuple[SearchRun, SearchProviderRun, SearchResult]:
        """Lock SearchRun, provider, and result in a stable order."""

        identifiers = (
            SearchResult.objects
            .filter(pk=search_result_id)
            .values("search_run_id", "provider_run_id")
            .get()
        )
        search_run = (
            SearchRun.objects
            .select_for_update()
            .get(pk=identifiers["search_run_id"])
        )
        provider_queryset = (
            SearchProviderRun.objects
            .select_for_update()
        )

        if include_related:
            provider_queryset = provider_queryset.select_related(
                "source"
            )

        provider_run = provider_queryset.get(
            pk=identifiers["provider_run_id"],
            search_run=search_run,
        )
        result_queryset = SearchResult.objects.select_for_update()

        if include_related:
            result_queryset = result_queryset.select_related(
                "source_dataset"
            )

        result = result_queryset.get(
            pk=search_result_id,
            provider_run=provider_run,
            search_run=search_run,
        )
        return search_run, provider_run, result

    @staticmethod
    def _optional_uuid(value: str | None) -> UUID | None:
        if not value:
            return None

        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _truncate(value: object, limit: int) -> str:
        return str(value)[:limit]


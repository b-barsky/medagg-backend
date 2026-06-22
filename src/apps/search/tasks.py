import random

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db.utils import OperationalError as DatabaseOperationalError

from apps.catalog.models import MetadataStatus
from apps.catalog.providers.exceptions import (
    ProviderError,
    ProviderUnavailableError,
)
from apps.catalog.services import (
    CatalogDatasetNotFoundError,
    CatalogService,
    CatalogSourceUnavailableError,
)

from .models import SearchProviderRun, SearchResult, SearchRun
from .services import (
    SearchResultPersistenceError,
    SearchRunService,
)


logger = get_task_logger(__name__)


def _retry_delay(
    retry_number: int,
    *,
    base_seconds: int,
    maximum_seconds: int,
) -> int:
    """Return exponential backoff with full jitter."""

    maximum = min(
        base_seconds * (2**retry_number),
        maximum_seconds,
    )
    return random.randint(1, max(1, maximum))


def _provider_retry_delay(retry_number: int) -> int:
    return _retry_delay(
        retry_number,
        base_seconds=(
            settings.SEARCH_PROVIDER_RETRY_BACKOFF_SECONDS
        ),
        maximum_seconds=(
            settings.SEARCH_PROVIDER_RETRY_BACKOFF_MAX_SECONDS
        ),
    )


def _detail_retry_delay(retry_number: int) -> int:
    return _retry_delay(
        retry_number,
        base_seconds=settings.SEARCH_DETAIL_RETRY_BACKOFF_SECONDS,
        maximum_seconds=(
            settings.SEARCH_DETAIL_RETRY_BACKOFF_MAX_SECONDS
        ),
    )


@shared_task(
    bind=True,
    name="search.provider",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=settings.SEARCH_PROVIDER_MAX_RETRIES,
    soft_time_limit=(
        settings.SEARCH_PROVIDER_SOFT_TIME_LIMIT_SECONDS
    ),
    time_limit=settings.SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS,
    rate_limit=settings.SEARCH_PROVIDER_RATE_LIMIT,
    autoretry_for=(DatabaseOperationalError,),
    retry_backoff=settings.SEARCH_PROVIDER_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=(
        settings.SEARCH_PROVIDER_RETRY_BACKOFF_MAX_SECONDS
    ),
    retry_jitter=True,
)
def search_provider(
    self,
    provider_run_id: int,
) -> None:
    """Fetch and publish provider summaries, then fan out detail tasks."""

    search_service = SearchRunService()

    try:
        execution = search_service.start_provider(
            provider_run_id,
            task_id=self.request.id,
        )
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable while starting provider search.",
            extra={
                "event": "search.provider.database_unavailable",
                "provider_run_id": provider_run_id,
                "task_id": self.request.id,
                "phase": "start",
            },
        )
        raise
    except (
        SearchProviderRun.DoesNotExist,
        SearchRun.DoesNotExist,
    ):
        logger.warning(
            "Skipped a provider task whose database record no longer exists.",
            extra={
                "event": "search.provider.missing",
                "provider_run_id": provider_run_id,
                "task_id": self.request.id,
            },
        )
        return

    if execution is None:
        logger.info(
            "Skipped a terminal, stale, or expired provider task.",
            extra={
                "event": "search.provider.skipped",
                "provider_run_id": provider_run_id,
                "task_id": self.request.id,
            },
        )
        return

    log_context = {
        "search_run_id": str(execution.search_run_id),
        "provider_run_id": execution.provider_run_id,
        "source_slug": execution.source_slug,
        "task_id": self.request.id,
        "attempt": execution.attempt_count,
    }

    logger.info(
        "Started provider summary search.",
        extra={
            "event": "search.provider.started",
            **log_context,
        },
    )

    try:
        sync_result = CatalogService().search_and_upsert(
            source_slug=execution.source_slug,
            query=execution.query,
            page=execution.provider_page,
        )
    except SoftTimeLimitExceeded as exc:
        if self.request.retries < self.max_retries:
            countdown = _provider_retry_delay(
                self.request.retries
            )
            retry_scheduled = (
                search_service.mark_provider_retrying(
                    execution.provider_run_id,
                    error_code="provider_soft_timeout",
                    error_message=(
                        "The provider summary request exceeded its soft "
                        "time limit."
                    ),
                )
            )

            if retry_scheduled:
                logger.warning(
                    "Provider summary timeout will be retried.",
                    extra={
                        "event": "search.provider.retry_scheduled",
                        "reason": "soft_timeout",
                        "retry_in_seconds": countdown,
                        "retry_number": self.request.retries + 1,
                        **log_context,
                    },
                )
                raise self.retry(
                    exc=exc,
                    countdown=countdown,
                )

            return

        search_service.fail_provider(
            execution.provider_run_id,
            error_code="provider_soft_timeout",
            error_message=(
                "The provider summary request repeatedly exceeded its "
                "soft time limit."
            ),
        )
        logger.exception(
            "Provider summary exhausted timeout retries.",
            extra={
                "event": "search.provider.retries_exhausted",
                "reason": "soft_timeout",
                **log_context,
            },
        )
        return
    except ProviderUnavailableError as exc:
        if self.request.retries < self.max_retries:
            countdown = _provider_retry_delay(
                self.request.retries
            )
            retry_scheduled = (
                search_service.mark_provider_retrying(
                    execution.provider_run_id,
                    error_code="provider_unavailable",
                    error_message=str(exc),
                )
            )

            if retry_scheduled:
                logger.warning(
                    "Provider summary search will be retried.",
                    extra={
                        "event": "search.provider.retry_scheduled",
                        "reason": "provider_unavailable",
                        "retry_in_seconds": countdown,
                        "retry_number": self.request.retries + 1,
                        **log_context,
                    },
                )
                raise self.retry(
                    exc=exc,
                    countdown=countdown,
                )

            return

        search_service.fail_provider(
            execution.provider_run_id,
            error_code="provider_unavailable",
            error_message=str(exc),
        )
        logger.exception(
            "Provider summary search exhausted its retries.",
            extra={
                "event": "search.provider.retries_exhausted",
                "reason": "provider_unavailable",
                **log_context,
            },
        )
        return
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable during provider summary search.",
            extra={
                "event": "search.provider.database_unavailable",
                "phase": "catalog_sync",
                **log_context,
            },
        )
        raise
    except (
        CatalogSourceUnavailableError,
        ProviderError,
        ValueError,
    ) as exc:
        search_service.fail_provider(
            execution.provider_run_id,
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        logger.exception(
            "Provider summary search failed permanently.",
            extra={
                "event": "search.provider.failed",
                **log_context,
            },
        )
        return
    except Exception:
        search_service.fail_provider(
            execution.provider_run_id,
            error_code="unexpected_error",
            error_message=(
                "The provider summary search failed unexpectedly."
            ),
        )
        logger.exception(
            "Provider summary search failed unexpectedly.",
            extra={
                "event": "search.provider.unexpected_error",
                **log_context,
            },
        )
        raise

    try:
        published = search_service.publish_provider_results(
            execution.provider_run_id,
            source_dataset_ids=sync_result.record_ids,
        )
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable while publishing summary results.",
            extra={
                "event": "search.provider.database_unavailable",
                "phase": "result_persistence",
                **log_context,
            },
        )
        raise
    except SearchResultPersistenceError as exc:
        search_service.fail_provider(
            execution.provider_run_id,
            error_code="result_persistence_failed",
            error_message=str(exc),
        )
        logger.exception(
            "Provider summary results could not be persisted.",
            extra={
                "event": "search.provider.persistence_failed",
                **log_context,
            },
        )
        return
    except Exception:
        search_service.fail_provider(
            execution.provider_run_id,
            error_code="unexpected_persistence_error",
            error_message=(
                "The provider summary results could not be stored."
            ),
        )
        logger.exception(
            "Provider result persistence failed unexpectedly.",
            extra={
                "event": "search.provider.persistence_unexpected_error",
                **log_context,
            },
        )
        raise

    if not published:
        logger.info(
            "Discarded provider summaries because the run was terminal.",
            extra={
                "event": "search.provider.result_discarded",
                **log_context,
            },
        )
        return

    dispatch = search_service.enqueue_result_enrichments(
        execution.provider_run_id
    )

    logger.info(
        "Published provider summaries and detail work.",
        extra={
            "event": "search.provider.summary_succeeded",
            "fetched_count": sync_result.fetched_count,
            "stored_count": sync_result.stored_count,
            "created_count": sync_result.created_count,
            "refreshed_count": sync_result.refreshed_count,
            "detail_tasks_queued": dispatch.queued_count,
            "detail_publish_failures": (
                dispatch.publish_failed_count
            ),
            **log_context,
        },
    )


@shared_task(
    bind=True,
    name="search.result.enrich",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=settings.SEARCH_DETAIL_MAX_RETRIES,
    soft_time_limit=settings.SEARCH_DETAIL_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS,
    rate_limit=settings.SEARCH_DETAIL_RATE_LIMIT,
    autoretry_for=(DatabaseOperationalError,),
    retry_backoff=settings.SEARCH_DETAIL_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=(
        settings.SEARCH_DETAIL_RETRY_BACKOFF_MAX_SECONDS
    ),
    retry_jitter=True,
)
def enrich_search_result(
    self,
    search_result_id: int,
) -> None:
    """Fetch complete provider metadata for one persisted search result."""

    search_service = SearchRunService()

    try:
        execution = search_service.start_result_enrichment(
            search_result_id,
            task_id=self.request.id,
        )
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable while starting detail enrichment.",
            extra={
                "event": "search.result.database_unavailable",
                "search_result_id": search_result_id,
                "task_id": self.request.id,
                "phase": "start",
            },
        )
        raise
    except (
        SearchResult.DoesNotExist,
        SearchProviderRun.DoesNotExist,
        SearchRun.DoesNotExist,
    ):
        logger.warning(
            "Skipped a detail task whose database record no longer exists.",
            extra={
                "event": "search.result.missing",
                "search_result_id": search_result_id,
                "task_id": self.request.id,
            },
        )
        return

    if execution is None:
        logger.info(
            "Skipped a terminal, stale, or expired detail task.",
            extra={
                "event": "search.result.skipped",
                "search_result_id": search_result_id,
                "task_id": self.request.id,
            },
        )
        return

    log_context = {
        "search_run_id": str(execution.search_run_id),
        "provider_run_id": execution.provider_run_id,
        "search_result_id": execution.search_result_id,
        "source_dataset_id": execution.source_dataset_id,
        "external_id": execution.external_id,
        "source_slug": execution.source_slug,
        "task_id": self.request.id,
        "attempt": execution.attempt_count,
    }

    logger.info(
        "Started detail metadata enrichment.",
        extra={
            "event": "search.result.enrichment_started",
            **log_context,
        },
    )

    try:
        source_dataset = CatalogService().fetch_and_enrich(
            execution.source_dataset_id
        )
    except SoftTimeLimitExceeded as exc:
        if self.request.retries < self.max_retries:
            countdown = _detail_retry_delay(
                self.request.retries
            )
            retry_scheduled = (
                search_service.mark_result_retrying(
                    execution.search_result_id,
                    error_code="detail_soft_timeout",
                    error_message=(
                        "The detail metadata request exceeded its soft "
                        "time limit."
                    ),
                )
            )

            if retry_scheduled:
                logger.warning(
                    "Detail timeout will be retried.",
                    extra={
                        "event": "search.result.retry_scheduled",
                        "reason": "soft_timeout",
                        "retry_in_seconds": countdown,
                        "retry_number": self.request.retries + 1,
                        **log_context,
                    },
                )
                raise self.retry(
                    exc=exc,
                    countdown=countdown,
                )

            return

        search_service.fail_result_enrichment(
            execution.search_result_id,
            error_code="detail_soft_timeout",
            error_message=(
                "The detail metadata request repeatedly exceeded its "
                "soft time limit."
            ),
        )
        logger.exception(
            "Detail metadata exhausted timeout retries.",
            extra={
                "event": "search.result.retries_exhausted",
                "reason": "soft_timeout",
                **log_context,
            },
        )
        return
    except ProviderUnavailableError as exc:
        if self.request.retries < self.max_retries:
            countdown = _detail_retry_delay(
                self.request.retries
            )
            retry_scheduled = (
                search_service.mark_result_retrying(
                    execution.search_result_id,
                    error_code="provider_unavailable",
                    error_message=str(exc),
                )
            )

            if retry_scheduled:
                logger.warning(
                    "Detail metadata request will be retried.",
                    extra={
                        "event": "search.result.retry_scheduled",
                        "reason": "provider_unavailable",
                        "retry_in_seconds": countdown,
                        "retry_number": self.request.retries + 1,
                        **log_context,
                    },
                )
                raise self.retry(
                    exc=exc,
                    countdown=countdown,
                )

            return

        search_service.fail_result_enrichment(
            execution.search_result_id,
            error_code="provider_unavailable",
            error_message=str(exc),
        )
        logger.exception(
            "Detail metadata request exhausted its retries.",
            extra={
                "event": "search.result.retries_exhausted",
                "reason": "provider_unavailable",
                **log_context,
            },
        )
        return
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable during detail enrichment.",
            extra={
                "event": "search.result.database_unavailable",
                "phase": "catalog_enrichment",
                **log_context,
            },
        )
        raise
    except (
        CatalogDatasetNotFoundError,
        CatalogSourceUnavailableError,
        ProviderError,
        ValueError,
    ) as exc:
        search_service.fail_result_enrichment(
            execution.search_result_id,
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        logger.exception(
            "Detail metadata enrichment failed permanently.",
            extra={
                "event": "search.result.enrichment_failed",
                **log_context,
            },
        )
        return
    except Exception:
        search_service.fail_result_enrichment(
            execution.search_result_id,
            error_code="unexpected_error",
            error_message=(
                "Detail metadata enrichment failed unexpectedly."
            ),
        )
        logger.exception(
            "Detail metadata enrichment failed unexpectedly.",
            extra={
                "event": "search.result.unexpected_error",
                **log_context,
            },
        )
        raise

    if source_dataset.detail_status != MetadataStatus.COMPLETE:
        error = SearchResultPersistenceError(
            "The provider returned metadata that became stale before it "
            "could be committed."
        )

        if self.request.retries < self.max_retries:
            countdown = _detail_retry_delay(
                self.request.retries
            )
            retry_scheduled = (
                search_service.mark_result_retrying(
                    execution.search_result_id,
                    error_code="detail_not_complete",
                    error_message=str(error),
                )
            )

            if retry_scheduled:
                logger.warning(
                    "Stale detail metadata will be retried.",
                    extra={
                        "event": "search.result.retry_scheduled",
                        "reason": "detail_not_complete",
                        "retry_in_seconds": countdown,
                        "retry_number": self.request.retries + 1,
                        **log_context,
                    },
                )
                raise self.retry(
                    exc=error,
                    countdown=countdown,
                )

            return

        search_service.fail_result_enrichment(
            execution.search_result_id,
            error_code="detail_not_complete",
            error_message=str(error),
        )
        logger.error(
            "Detail metadata remained incomplete after retries.",
            extra={
                "event": "search.result.enrichment_incomplete",
                **log_context,
            },
        )
        return

    try:
        completed = search_service.complete_result_enrichment(
            execution.search_result_id
        )
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable while completing detail enrichment.",
            extra={
                "event": "search.result.database_unavailable",
                "phase": "completion",
                **log_context,
            },
        )
        raise
    except SearchResultPersistenceError as exc:
        search_service.fail_result_enrichment(
            execution.search_result_id,
            error_code="detail_completion_failed",
            error_message=str(exc),
        )
        logger.exception(
            "Detail metadata completion could not be persisted.",
            extra={
                "event": "search.result.persistence_failed",
                **log_context,
            },
        )
        return

    if not completed:
        logger.info(
            "Discarded detail completion because the run was terminal.",
            extra={
                "event": "search.result.completion_discarded",
                **log_context,
            },
        )
        return

    logger.info(
        "Completed detail metadata enrichment.",
        extra={
            "event": "search.result.enrichment_succeeded",
            **log_context,
        },
    )


@shared_task(
    name="search.run.expire",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(DatabaseOperationalError,),
    retry_kwargs={"max_retries": 5},
    retry_backoff=5,
    retry_backoff_max=60,
    retry_jitter=True,
)
def expire_search_run(search_run_id: str) -> None:
    """Reconcile an overdue run even when no browser is polling it."""

    try:
        changed = SearchRunService().expire_run_if_needed(
            search_run_id
        )
    except SearchRun.DoesNotExist:
        logger.info(
            "Skipped a deadline task for a deleted search run.",
            extra={
                "event": "search.run.deadline_missing",
                "search_run_id": search_run_id,
            },
        )
        return

    logger.info(
        "Processed search run deadline.",
        extra={
            "event": "search.run.deadline_processed",
            "search_run_id": search_run_id,
            "changed": changed,
        },
    )

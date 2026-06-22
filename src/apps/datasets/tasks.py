import random

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db.utils import OperationalError as DatabaseOperationalError

from apps.catalog.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderDownloadUnsupportedError,
    ProviderResponseError,
    ProviderUnavailableError,
)

from .access import DatasetAccessService
from .models import DatasetImport
from .services import (
    DatasetImportError,
    DatasetImportPolicyRejected,
    DatasetImportService,
    DatasetImportSourceChanged,
    DatasetImportTaskMismatch,
    DatasetImportTooLarge,
)
from .storage import ObjectStorageError


logger = get_task_logger(__name__)


def _retry_delay(retry_number: int) -> int:
    maximum = min(
        settings.DATASET_IMPORT_RETRY_BACKOFF_SECONDS
        * (2**retry_number),
        settings.DATASET_IMPORT_RETRY_BACKOFF_MAX_SECONDS,
    )
    return random.randint(1, max(1, maximum))


def _grant_requester_access(
    import_id: str,
    log_context: dict[str, object],
) -> int:
    try:
        granted_count = DatasetAccessService().grant_import_access(
            import_id
        )
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable while granting dataset access.",
            extra={
                **log_context,
                "event": "datasets.import.access_database_unavailable",
            },
        )
        raise
    except Exception:
        # Polling the import endpoint performs the same idempotent grant, so a
        # non-database bookkeeping failure must not invalidate a successful
        # artifact import.
        logger.exception(
            "Dataset import succeeded but access bookkeeping failed.",
            extra={
                **log_context,
                "event": "datasets.import.access_grant_failed",
            },
        )
        return 0

    logger.info(
        "Granted completed dataset access to import requesters.",
        extra={
            **log_context,
            "event": "datasets.import.access_granted",
            "granted_count": granted_count,
        },
    )
    return granted_count


@shared_task(
    bind=True,
    name="datasets.import",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=settings.DATASET_IMPORT_MAX_RETRIES,
    soft_time_limit=settings.DATASET_IMPORT_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.DATASET_IMPORT_HARD_TIME_LIMIT_SECONDS,
    rate_limit=settings.DATASET_IMPORT_RATE_LIMIT,
    autoretry_for=(DatabaseOperationalError,),
    retry_backoff=settings.DATASET_IMPORT_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=(
        settings.DATASET_IMPORT_RETRY_BACKOFF_MAX_SECONDS
    ),
    retry_jitter=True,
)
def import_dataset_artifact(
    self,
    import_id: str,
) -> None:
    service = DatasetImportService()
    log_context = {
        "event": "datasets.import.started",
        "import_id": import_id,
        "task_id": self.request.id,
        "attempt": self.request.retries + 1,
    }

    logger.info(
        "Started dataset artifact import.",
        extra=log_context,
    )

    try:
        result = service.execute_import(
            import_id,
            task_id=self.request.id,
        )
    except DatasetImport.DoesNotExist:
        logger.warning(
            "Skipped an import whose database record no longer exists.",
            extra={
                **log_context,
                "event": "datasets.import.missing",
            },
        )
        return
    except DatasetImportTaskMismatch as exc:
        logger.warning(
            "Skipped a stale dataset import task.",
            extra={
                **log_context,
                "event": "datasets.import.stale_task",
                "error": str(exc),
            },
        )
        return
    except DatasetImportPolicyRejected as exc:
        service.mark_rejected(
            import_id,
            error_code=exc.decision.code,
            error_message=exc.decision.message,
        )
        logger.warning(
            "Dataset import was rejected by policy.",
            extra={
                **log_context,
                "event": "datasets.import.policy_rejected",
                "policy_code": exc.decision.code,
            },
        )
        return
    except DatasetImportSourceChanged as exc:
        service.mark_rejected(
            import_id,
            error_code="source_changed",
            error_message=str(exc),
        )
        logger.warning(
            "Dataset import was rejected because the source changed.",
            extra={
                **log_context,
                "event": "datasets.import.source_changed",
            },
        )
        return
    except DatasetImportTooLarge as exc:
        service.mark_rejected(
            import_id,
            error_code="size_limit_exceeded",
            error_message=str(exc),
        )
        logger.warning(
            "Dataset import exceeded the size limit.",
            extra={
                **log_context,
                "event": "datasets.import.size_rejected",
            },
        )
        return
    except (
        ProviderAuthenticationError,
        ProviderConfigurationError,
        ProviderDownloadUnsupportedError,
        ProviderResponseError,
    ) as exc:
        service.mark_failed(
            import_id,
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        logger.exception(
            "Dataset import failed permanently at the provider layer.",
            extra={
                **log_context,
                "event": "datasets.import.provider_failed",
            },
        )
        return
    except SoftTimeLimitExceeded as exc:
        if self.request.retries < self.max_retries:
            countdown = _retry_delay(self.request.retries)
            service.mark_retrying(
                import_id,
                error_code="soft_timeout",
                error_message=(
                    "The import exceeded its soft time limit and will retry."
                ),
            )
            logger.warning(
                "Dataset import timeout will be retried.",
                extra={
                    **log_context,
                    "event": "datasets.import.retry_scheduled",
                    "reason": "soft_timeout",
                    "retry_in_seconds": countdown,
                },
            )
            raise self.retry(exc=exc, countdown=countdown)

        service.mark_failed(
            import_id,
            error_code="soft_timeout",
            error_message=(
                "The import repeatedly exceeded its soft time limit."
            ),
        )
        logger.exception(
            "Dataset import exhausted timeout retries.",
            extra={
                **log_context,
                "event": "datasets.import.retries_exhausted",
                "reason": "soft_timeout",
            },
        )
        return
    except (ProviderUnavailableError, ObjectStorageError) as exc:
        if self.request.retries < self.max_retries:
            countdown = _retry_delay(self.request.retries)
            service.mark_retrying(
                import_id,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            logger.warning(
                "Dataset import will be retried.",
                extra={
                    **log_context,
                    "event": "datasets.import.retry_scheduled",
                    "reason": type(exc).__name__,
                    "retry_in_seconds": countdown,
                },
            )
            raise self.retry(exc=exc, countdown=countdown)

        service.mark_failed(
            import_id,
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        logger.exception(
            "Dataset import exhausted operational retries.",
            extra={
                **log_context,
                "event": "datasets.import.retries_exhausted",
                "reason": type(exc).__name__,
            },
        )
        return
    except DatabaseOperationalError:
        logger.exception(
            "Database unavailable during dataset import.",
            extra={
                **log_context,
                "event": "datasets.import.database_unavailable",
            },
        )
        raise
    except DatasetImportError as exc:
        service.mark_failed(
            import_id,
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        logger.exception(
            "Dataset import failed permanently.",
            extra={
                **log_context,
                "event": "datasets.import.failed",
            },
        )
        return
    except Exception as exc:
        if self.request.retries < self.max_retries:
            countdown = _retry_delay(self.request.retries)
            service.mark_retrying(
                import_id,
                error_code="unexpected_error",
                error_message=(
                    "The import failed unexpectedly and will retry."
                ),
            )
            logger.exception(
                "Unexpected dataset import failure will be retried.",
                extra={
                    **log_context,
                    "event": "datasets.import.retry_scheduled",
                    "reason": "unexpected_error",
                    "retry_in_seconds": countdown,
                },
            )
            raise self.retry(exc=exc, countdown=countdown)

        service.mark_failed(
            import_id,
            error_code="unexpected_error",
            error_message="The import failed unexpectedly.",
        )
        logger.exception(
            "Dataset import failed unexpectedly.",
            extra={
                **log_context,
                "event": "datasets.import.unexpected_error",
            },
        )
        return

    if result is None:
        _grant_requester_access(import_id, log_context)
        logger.info(
            "Skipped a terminal dataset import.",
            extra={
                **log_context,
                "event": "datasets.import.skipped",
            },
        )
        return

    granted_count = _grant_requester_access(import_id, log_context)

    logger.info(
        "Dataset artifact import completed.",
        extra={
            **log_context,
            "event": "datasets.import.succeeded",
            "dataset_id": result.dataset_id,
            "dataset_version_id": str(result.dataset_version_id),
            "artifact_id": str(result.artifact_id),
            "checksum_sha256": result.checksum_sha256,
            "size_bytes": result.size_bytes,
            "object_reused": result.object_reused,
            "version_reused": result.version_reused,
            "granted_count": granted_count,
        },
    )

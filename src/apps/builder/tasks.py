from __future__ import annotations

import logging
import uuid

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.datasets.models import DatasetVersion, DatasetVersionStatus

from .execution import (
    BuildExecutionError,
    BuildExecutionRejected,
    BuildExecutionService,
    BuildTransientError,
)
from .models import (
    AnalysisStatus,
    BuildRequest,
    BuildRequestStatus,
    BuildRun,
    BuildRunStatus,
    DatasetAnalysis,
)
from .planning import (
    BuildAnalysisPending,
    BuildPlanningError,
    BuildPlanningService,
)
from .schema import DatasetSchemaAnalyzer


logger = logging.getLogger(__name__)


def _backoff(base: int, maximum: int, retries: int) -> int:
    return min(maximum, base * (2**retries))


def enqueue_analysis_for_version(dataset_version_id) -> DatasetAnalysis:
    analysis, _ = DatasetAnalysis.objects.get_or_create(
        dataset_version_id=dataset_version_id,
        defaults={"status": AnalysisStatus.PENDING},
    )
    if analysis.status == AnalysisStatus.COMPLETE:
        return analysis
    task_id = str(uuid.uuid4())
    updated = DatasetAnalysis.objects.filter(
        pk=analysis.pk,
        status__in=(
            AnalysisStatus.PENDING,
            AnalysisStatus.FAILED,
            AnalysisStatus.UNSUPPORTED,
        ),
    ).update(
        status=AnalysisStatus.QUEUED,
        celery_task_id=task_id,
        progress=0,
        progress_message="Queued for schema discovery",
        queued_at=timezone.now(),
        error_code="",
        error_message="",
        finished_at=None,
        updated_at=timezone.now(),
    )
    if updated:
        transaction.on_commit(
            lambda: analyze_dataset_version.apply_async(
                args=(str(dataset_version_id),),
                task_id=task_id,
                queue=settings.BUILDER_ANALYSIS_TASK_QUEUE,
            )
        )
    return DatasetAnalysis.objects.get(pk=analysis.pk)


def enqueue_build_request(request_id) -> None:
    task_id = str(uuid.uuid4())
    BuildRequest.objects.filter(pk=request_id).update(
        celery_task_id=task_id,
        status=BuildRequestStatus.QUEUED,
        progress=0,
        progress_message="Queued for AI planning",
        updated_at=timezone.now(),
    )
    transaction.on_commit(
        lambda: plan_build_request.apply_async(
            args=(str(request_id),),
            task_id=task_id,
            queue=settings.BUILDER_TASK_QUEUE,
        )
    )


def enqueue_build_run(run_id) -> None:
    task_id = str(uuid.uuid4())
    BuildRun.objects.filter(pk=run_id).update(
        celery_task_id=task_id,
        status=BuildRunStatus.QUEUED,
        progress=0,
        progress_message="Queued for execution",
        updated_at=timezone.now(),
    )
    transaction.on_commit(
        lambda: execute_build_run.apply_async(
            args=(str(run_id),),
            task_id=task_id,
            queue=settings.BUILDER_TASK_QUEUE,
        )
    )


@shared_task(
    bind=True,
    name="builder.dataset.analyze",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.BUILDER_ANALYSIS_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.BUILDER_ANALYSIS_HARD_TIME_LIMIT_SECONDS,
)
def analyze_dataset_version(self, dataset_version_id: str) -> None:
    logger.info(
        "builder.analysis.started",
        extra={
            "event": "builder.analysis.started",
            "dataset_version_id": dataset_version_id,
            "task_id": self.request.id,
        },
    )
    try:
        DatasetSchemaAnalyzer().analyze(dataset_version_id)
    except SoftTimeLimitExceeded:
        DatasetAnalysis.objects.filter(
            dataset_version_id=dataset_version_id
        ).update(
            status=AnalysisStatus.FAILED,
            progress=100,
            progress_message="Analysis timed out",
            error_code="analysis_timeout",
            error_message="Schema discovery exceeded its time limit.",
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        raise
    except Exception as exc:
        DatasetAnalysis.objects.filter(
            dataset_version_id=dataset_version_id
        ).update(
            status=AnalysisStatus.FAILED,
            progress=100,
            progress_message="Analysis failed",
            error_code="analysis_unhandled_error",
            error_message=str(exc),
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        logger.exception(
            "builder.analysis.failed",
            extra={
                "event": "builder.analysis.failed",
                "dataset_version_id": dataset_version_id,
                "task_id": self.request.id,
            },
        )
        raise


@shared_task(
    bind=True,
    name="builder.request.plan",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=settings.BUILDER_PLANNING_MAX_RETRIES,
    soft_time_limit=settings.BUILDER_PLANNING_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.BUILDER_PLANNING_HARD_TIME_LIMIT_SECONDS,
)
def plan_build_request(self, request_id: str) -> None:
    try:
        BuildPlanningService().plan(request_id)
    except BuildAnalysisPending as exc:
        countdown = _backoff(
            settings.BUILDER_PLANNING_RETRY_BACKOFF_SECONDS,
            settings.BUILDER_PLANNING_RETRY_BACKOFF_MAX_SECONDS,
            self.request.retries,
        )
        BuildRequest.objects.filter(pk=request_id).update(
            status=BuildRequestStatus.PLANNING,
            progress_message=str(exc)[:255],
            updated_at=timezone.now(),
        )
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except self.MaxRetriesExceededError:
            BuildRequest.objects.filter(pk=request_id).update(
                status=BuildRequestStatus.FAILED,
                progress=100,
                progress_message="Required schema analyses did not finish",
                error_code="analysis_timeout",
                error_message=str(exc),
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
    except BuildPlanningError as exc:
        BuildRequest.objects.filter(pk=request_id).exclude(
            status=BuildRequestStatus.REJECTED
        ).update(
            status=BuildRequestStatus.REJECTED,
            progress=100,
            progress_message=str(exc)[:255],
            error_code=exc.code,
            error_message=str(exc),
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
    except SoftTimeLimitExceeded:
        BuildRequest.objects.filter(pk=request_id).update(
            status=BuildRequestStatus.FAILED,
            progress=100,
            progress_message="Planning timed out",
            error_code="planning_timeout",
            error_message="AI planning exceeded its time limit.",
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        raise
    except Exception as exc:
        BuildRequest.objects.filter(pk=request_id).update(
            status=BuildRequestStatus.FAILED,
            progress=100,
            progress_message="Planning failed",
            error_code="planning_unhandled_error",
            error_message=str(exc),
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        logger.exception(
            "builder.request.failed",
            extra={
                "event": "builder.request.failed",
                "build_request_id": request_id,
                "task_id": self.request.id,
            },
        )
        raise


@shared_task(
    bind=True,
    name="builder.run.execute",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=settings.BUILDER_BUILD_MAX_RETRIES,
    soft_time_limit=settings.BUILDER_BUILD_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.BUILDER_BUILD_HARD_TIME_LIMIT_SECONDS,
)
def execute_build_run(self, run_id: str) -> None:
    try:
        BuildExecutionService().execute(run_id)
    except BuildExecutionRejected as exc:
        _finish_run_failure(run_id, BuildRunStatus.REJECTED, exc.code, str(exc))
    except BuildTransientError as exc:
        countdown = _backoff(
            settings.BUILDER_BUILD_RETRY_BACKOFF_SECONDS,
            settings.BUILDER_BUILD_RETRY_BACKOFF_MAX_SECONDS,
            self.request.retries,
        )
        BuildRun.objects.filter(pk=run_id).update(
            status=BuildRunStatus.RETRYING,
            progress_message=f"Retrying: {exc}"[:255],
            error_code=exc.code,
            error_message=str(exc),
            updated_at=timezone.now(),
        )
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except self.MaxRetriesExceededError:
            _finish_run_failure(run_id, BuildRunStatus.FAILED, exc.code, str(exc))
    except BuildExecutionError as exc:
        _finish_run_failure(run_id, BuildRunStatus.FAILED, exc.code, str(exc))
    except SoftTimeLimitExceeded:
        _finish_run_failure(
            run_id,
            BuildRunStatus.FAILED,
            "build_timeout",
            "Dataset build exceeded its time limit.",
        )
        raise
    except Exception as exc:
        _finish_run_failure(
            run_id,
            BuildRunStatus.FAILED,
            "build_unhandled_error",
            str(exc),
        )
        logger.exception(
            "builder.run.failed",
            extra={
                "event": "builder.run.failed",
                "build_run_id": run_id,
                "task_id": self.request.id,
            },
        )
        raise


def _finish_run_failure(run_id, status, code, message) -> None:
    with transaction.atomic():
        run = BuildRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None or run.status == BuildRunStatus.SUCCEEDED:
            return
        BuildRun.objects.filter(pk=run_id).update(
            status=status,
            progress=100,
            progress_message=message[:255],
            error_code=code,
            error_message=message,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        if run.output_version_id is not None:
            DatasetVersion.objects.filter(
                pk=run.output_version_id,
            ).exclude(status=DatasetVersionStatus.AVAILABLE).update(
                status=DatasetVersionStatus.FAILED,
                error_code=code,
                error_message=message,
            )
        BuildRequest.objects.filter(pk=run.request_id).update(
            status=(
                BuildRequestStatus.REJECTED
                if status == BuildRunStatus.REJECTED
                else BuildRequestStatus.FAILED
            ),
            progress=100,
            progress_message=message[:255],
            error_code=code,
            error_message=message,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Prefetch, QuerySet
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError

from apps.catalog.models import SourceDataset
from apps.catalog.providers import provider_registry
from apps.catalog.providers.registry import ProviderRegistry

from .models import (
    Dataset,
    DatasetArtifact,
    DatasetArtifactKind,
    DatasetImport,
    DatasetImportStatus,
    DatasetOrigin,
    DatasetVersion,
    DatasetVersionStatus,
)
from .policies import DatasetImportPolicy, ImportPolicyDecision
from .revisions import source_dataset_revision
from .storage import S3ObjectStorage, StoredObject


logger = logging.getLogger(__name__)

BROKER_ERRORS = (
    BrokerOperationalError,
    ConnectionError,
    TimeoutError,
    OSError,
)


class DatasetImportError(RuntimeError):
    """Base error for durable dataset imports."""


class DatasetImportPolicyRejected(DatasetImportError):
    def __init__(self, decision: ImportPolicyDecision) -> None:
        super().__init__(decision.message)
        self.decision = decision


class DatasetImportSourceChanged(DatasetImportError):
    """The source revision or license changed during an import."""


class DatasetImportTooLarge(DatasetImportError):
    """The downloaded artifact exceeds the configured size limit."""


class DatasetImportTaskMismatch(DatasetImportError):
    """A stale/redelivered Celery message does not own the import run."""


@dataclass(frozen=True, slots=True)
class ImportCreationResult:
    import_run: DatasetImport
    created: bool
    reused_version: bool


@dataclass(frozen=True, slots=True)
class ImportExecutionContext:
    import_id: UUID
    source_dataset_id: int
    source_slug: str
    external_id: str
    dataset_id: int
    dataset_version_id: UUID
    source_revision: str
    attempt_count: int
    already_available: bool


@dataclass(frozen=True, slots=True)
class ImportExecutionResult:
    import_id: UUID
    dataset_id: int
    dataset_version_id: UUID
    artifact_id: UUID
    checksum_sha256: str
    size_bytes: int
    object_reused: bool
    version_reused: bool


class DatasetService:
    def get_all_detailed(self) -> QuerySet[Dataset]:
        available_versions = DatasetVersion.objects.filter(
            status=DatasetVersionStatus.AVAILABLE
        ).prefetch_related("artifacts")

        return (
            Dataset.objects.filter(
                versions__status=DatasetVersionStatus.AVAILABLE
            )
            .select_related("anatomical_area", "source_dataset__source")
            .prefetch_related(
                "modalities",
                "ml_tasks",
                "tags",
                Prefetch(
                    "versions",
                    queryset=available_versions,
                    to_attr="available_versions",
                ),
            )
            .distinct()
            .order_by("-created_at", "-id")
        )


class DatasetImportService:
    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        storage: S3ObjectStorage | None = None,
        policy: DatasetImportPolicy | None = None,
    ) -> None:
        self._registry = provider_registry if registry is None else registry
        self._storage = S3ObjectStorage() if storage is None else storage
        self._policy = DatasetImportPolicy() if policy is None else policy

    def get_import(self, import_id: UUID | str) -> DatasetImport:
        return (
            DatasetImport.objects.select_related(
                "source_dataset__source",
                "dataset",
                "dataset_version",
            )
            .prefetch_related("dataset_version__artifacts")
            .get(pk=import_id)
        )

    def create_import(
        self,
        *,
        source_dataset_id: int,
        accepted_license: bool,
        license_fingerprint: str,
        requested_by=None,
    ) -> ImportCreationResult:
        source_revision: str | None = None

        try:
            with transaction.atomic():
                source_dataset = (
                    SourceDataset.objects.select_for_update()
                    .select_related("source")
                    .get(pk=source_dataset_id)
                )

                decision = self._policy.evaluate(
                    source_dataset,
                    accepted_license=accepted_license,
                    presented_license_fingerprint=license_fingerprint,
                    private_access_authorized=bool(
                        getattr(requested_by, "is_staff", False)
                    ),
                )

                if not decision.allowed:
                    raise DatasetImportPolicyRejected(decision)

                source_revision = decision.source_revision
                audit_user = (
                    requested_by
                    if getattr(requested_by, "is_authenticated", False)
                    else None
                )
                existing_success = (
                    DatasetImport.objects.select_related(
                        "dataset_version"
                    )
                    .filter(
                        source_dataset=source_dataset,
                        source_revision=decision.source_revision,
                        status=DatasetImportStatus.SUCCEEDED,
                        dataset_version__status=(
                            DatasetVersionStatus.AVAILABLE
                        ),
                    )
                    .order_by("-created_at")
                    .first()
                )

                if existing_success is not None:
                    now = timezone.now()
                    reuse_run = DatasetImport.objects.create(
                        source_dataset=source_dataset,
                        dataset=existing_success.dataset,
                        dataset_version=existing_success.dataset_version,
                        requested_by=audit_user,
                        status=DatasetImportStatus.SUCCEEDED,
                        source_revision=decision.source_revision,
                        source_version=source_dataset.remote_version,
                        source_updated_at=source_dataset.remote_updated_at,
                        accepted_license=True,
                        license_names_snapshot=list(
                            decision.license_names
                        ),
                        license_fingerprint=(
                            decision.license_fingerprint
                        ),
                        policy_snapshot=decision.as_dict(),
                        started_at=now,
                        last_attempt_at=now,
                        finished_at=now,
                    )
                    return ImportCreationResult(
                        import_run=reuse_run,
                        created=False,
                        reused_version=True,
                    )

                existing_active = (
                    DatasetImport.objects.filter(
                        source_dataset=source_dataset,
                        source_revision=decision.source_revision,
                        status__in=DatasetImport.ACTIVE_STATUSES,
                    )
                    .order_by("-created_at")
                    .first()
                )

                if existing_active is not None:
                    return ImportCreationResult(
                        import_run=existing_active,
                        created=False,
                        reused_version=False,
                    )

                import_run = DatasetImport.objects.create(
                    source_dataset=source_dataset,
                    requested_by=audit_user,
                    status=DatasetImportStatus.QUEUED,
                    source_revision=decision.source_revision,
                    source_version=source_dataset.remote_version,
                    source_updated_at=source_dataset.remote_updated_at,
                    accepted_license=True,
                    license_names_snapshot=list(decision.license_names),
                    license_fingerprint=decision.license_fingerprint,
                    policy_snapshot=decision.as_dict(),
                )
        except IntegrityError:
            # A concurrent request won the active-import uniqueness race.
            if source_revision is None:
                raise

            import_run = (
                DatasetImport.objects.filter(
                    source_dataset_id=source_dataset_id,
                    source_revision=source_revision,
                    status__in=DatasetImport.ACTIVE_STATUSES,
                )
                .order_by("-created_at")
                .first()
            )

            if import_run is None:
                raise

            return ImportCreationResult(
                import_run=import_run,
                created=False,
                reused_version=False,
            )

        return ImportCreationResult(
            import_run=import_run,
            created=True,
            reused_version=False,
        )

    def enqueue_import(self, import_id: UUID | str) -> str:
        from .tasks import import_dataset_artifact

        task_id = str(uuid4())

        updated = DatasetImport.objects.filter(
            pk=import_id,
            status=DatasetImportStatus.QUEUED,
            celery_task_id="",
        ).update(celery_task_id=task_id)

        if not updated:
            return str(
                DatasetImport.objects.only("celery_task_id")
                .get(pk=import_id)
                .celery_task_id
            )

        try:
            import_dataset_artifact.apply_async(
                args=(str(import_id),),
                task_id=task_id,
                queue=settings.DATASET_IMPORT_TASK_QUEUE,
            )
        except BROKER_ERRORS as exc:
            self.mark_failed(
                import_id,
                error_code="broker_publish_failed",
                error_message=(
                    "The import could not be sent to the background worker."
                ),
            )
            raise DatasetImportError(
                "The import worker is currently unavailable."
            ) from exc

        return task_id

    def execute_import(
        self,
        import_id: UUID | str,
        *,
        task_id: str,
    ) -> ImportExecutionResult | None:
        context = self._start_import(import_id, task_id=task_id)

        if context is None:
            return None

        if context.already_available:
            import_run = self.get_import(context.import_id)
            artifact = import_run.dataset_version.artifacts.first()

            if artifact is None:
                raise DatasetImportError(
                    "An available dataset version has no artifact."
                )

            return ImportExecutionResult(
                import_id=context.import_id,
                dataset_id=context.dataset_id,
                dataset_version_id=context.dataset_version_id,
                artifact_id=artifact.pk,
                checksum_sha256=artifact.checksum_sha256,
                size_bytes=artifact.size_bytes,
                object_reused=True,
                version_reused=True,
            )

        source_dataset = (
            SourceDataset.objects.select_related("source")
            .get(pk=context.source_dataset_id)
        )
        self._assert_policy_still_allows(source_dataset, context)
        provider = self._registry.create(context.source_slug)
        try:
            with TemporaryDirectory(
                prefix="medagg-dataset-import-"
            ) as directory:
                downloaded = provider.download_artifact(
                    context.external_id,
                    Path(directory),
                )
                artifact_path = downloaded.path.resolve()
                temporary_root = Path(directory).resolve()

                if (
                    not artifact_path.is_file()
                    or temporary_root not in artifact_path.parents
                ):
                    raise DatasetImportError(
                        "The provider returned an invalid artifact path."
                    )

                checksum_sha256, size_bytes = self._hash_file(
                    artifact_path,
                    maximum_bytes=settings.DATASET_IMPORT_MAX_BYTES,
                )

                stored_object = self._storage.store_file(
                    artifact_path,
                    dataset_id=context.dataset_id,
                    filename=downloaded.filename,
                    content_type=downloaded.content_type,
                    size_bytes=size_bytes,
                    checksum_sha256=checksum_sha256,
                )

                return self._complete_import(
                    context,
                    stored_object=stored_object,
                    filename=downloaded.filename,
                    content_type=downloaded.content_type,
                )
        except Exception:
            # Do not delete a freshly uploaded content-addressed object here.
            # Another concurrent version may already reference the same object.
            # A future garbage-collection job can remove unreferenced objects
            # safely after checking database reachability.
            raise

    def mark_retrying(
        self,
        import_id: UUID | str,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        return bool(
            DatasetImport.objects.filter(
                pk=import_id,
                status__in=(
                    DatasetImportStatus.RUNNING,
                    DatasetImportStatus.RETRYING,
                ),
            ).update(
                status=DatasetImportStatus.RETRYING,
                error_code=error_code[:64],
                error_message=error_message,
                finished_at=None,
            )
        )

    def mark_failed(
        self,
        import_id: UUID | str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self._finish_with_error(
            import_id,
            status=DatasetImportStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    def mark_rejected(
        self,
        import_id: UUID | str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self._finish_with_error(
            import_id,
            status=DatasetImportStatus.REJECTED,
            error_code=error_code,
            error_message=error_message,
        )

    def _start_import(
        self,
        import_id: UUID | str,
        *,
        task_id: str,
    ) -> ImportExecutionContext | None:
        now = timezone.now()

        with transaction.atomic():
            import_run = (
                DatasetImport.objects.select_for_update()
                .select_related("source_dataset__source")
                .get(pk=import_id)
            )

            if import_run.is_terminal:
                return None

            if (
                import_run.celery_task_id
                and import_run.celery_task_id != task_id
            ):
                raise DatasetImportTaskMismatch(
                    "The Celery task ID does not own this import run."
                )

            source_dataset = import_run.source_dataset
            current_revision = source_dataset_revision(source_dataset)

            if current_revision != import_run.source_revision:
                raise DatasetImportSourceChanged(
                    "The remote dataset revision changed before download."
                )

            decision = self._policy.evaluate(
                source_dataset,
                accepted_license=import_run.accepted_license,
                presented_license_fingerprint=(
                    import_run.license_fingerprint
                ),
                private_access_authorized=bool(
                    import_run.policy_snapshot.get(
                        "private_access_authorized",
                        False,
                    )
                ),
            )

            if not decision.allowed:
                raise DatasetImportPolicyRejected(decision)

            dataset, _ = Dataset.objects.select_for_update().get_or_create(
                source_dataset=source_dataset,
                defaults=self._dataset_defaults(source_dataset),
            )
            self._refresh_dataset_metadata(dataset, source_dataset)

            version = (
                DatasetVersion.objects.select_for_update()
                .filter(
                    dataset=dataset,
                    source_revision=import_run.source_revision,
                )
                .first()
            )

            already_available = bool(
                version is not None
                and version.status == DatasetVersionStatus.AVAILABLE
                and version.artifacts.exists()
            )

            if version is None:
                next_number = (
                    DatasetVersion.objects.filter(dataset=dataset)
                    .aggregate(maximum=Max("number"))["maximum"]
                    or 0
                ) + 1
                version = DatasetVersion.objects.create(
                    dataset=dataset,
                    number=next_number,
                    status=DatasetVersionStatus.STAGING,
                    source_revision=import_run.source_revision,
                    source_version=import_run.source_version,
                    source_updated_at=import_run.source_updated_at,
                )
            elif not already_available:
                version.status = DatasetVersionStatus.STAGING
                version.error_code = ""
                version.error_message = ""
                version.save(
                    update_fields=(
                        "status",
                        "error_code",
                        "error_message",
                        "updated_at",
                    )
                )

            import_run.dataset = dataset
            import_run.dataset_version = version
            import_run.status = (
                DatasetImportStatus.SUCCEEDED
                if already_available
                else DatasetImportStatus.RUNNING
            )
            import_run.attempt_count += 1
            import_run.started_at = import_run.started_at or now
            import_run.last_attempt_at = now
            import_run.finished_at = now if already_available else None
            import_run.error_code = ""
            import_run.error_message = ""
            import_run.save(
                update_fields=(
                    "dataset",
                    "dataset_version",
                    "status",
                    "attempt_count",
                    "started_at",
                    "last_attempt_at",
                    "finished_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                )
            )

            return ImportExecutionContext(
                import_id=import_run.pk,
                source_dataset_id=source_dataset.pk,
                source_slug=source_dataset.source.slug,
                external_id=source_dataset.external_id,
                dataset_id=dataset.pk,
                dataset_version_id=version.pk,
                source_revision=import_run.source_revision,
                attempt_count=import_run.attempt_count,
                already_available=already_available,
            )

    def _complete_import(
        self,
        context: ImportExecutionContext,
        *,
        stored_object: StoredObject,
        filename: str,
        content_type: str,
    ) -> ImportExecutionResult:
        now = timezone.now()

        with transaction.atomic():
            import_run = (
                DatasetImport.objects.select_for_update()
                .select_related("source_dataset__source")
                .get(pk=context.import_id)
            )
            source_dataset = (
                SourceDataset.objects.select_for_update()
                .select_related("source")
                .get(pk=context.source_dataset_id)
            )

            self._assert_policy_still_allows(source_dataset, context)

            version = DatasetVersion.objects.select_for_update().get(
                pk=context.dataset_version_id
            )
            dataset = Dataset.objects.select_for_update().get(
                pk=context.dataset_id
            )

            artifact, _ = DatasetArtifact.objects.get_or_create(
                dataset_version=version,
                kind=DatasetArtifactKind.SOURCE_ARCHIVE,
                object_key=stored_object.object_key,
                defaults={
                    "storage_backend": "s3",
                    "bucket": stored_object.bucket,
                    "object_version_id": (
                        stored_object.object_version_id
                    ),
                    "filename": Path(filename).name[:500],
                    "content_type": (
                        content_type or "application/octet-stream"
                    )[:255],
                    "size_bytes": stored_object.size_bytes,
                    "checksum_sha256": (
                        stored_object.checksum_sha256
                    ),
                    "etag": stored_object.etag,
                },
            )

            version.status = DatasetVersionStatus.AVAILABLE
            version.checksum_sha256 = stored_object.checksum_sha256
            version.size_bytes = stored_object.size_bytes
            version.manifest = {
                "source": {
                    "slug": source_dataset.source.slug,
                    "external_id": source_dataset.external_id,
                    "source_url": source_dataset.source_url,
                    "remote_version": source_dataset.remote_version,
                    "remote_updated_at": (
                        source_dataset.remote_updated_at.isoformat()
                        if source_dataset.remote_updated_at
                        else None
                    ),
                    "revision": context.source_revision,
                },
                "artifact": {
                    "filename": artifact.filename,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                    "checksum_sha256": artifact.checksum_sha256,
                    "bucket": artifact.bucket,
                    "object_key": artifact.object_key,
                    "object_version_id": artifact.object_version_id,
                },
                "license": {
                    "names": list(import_run.license_names_snapshot),
                    "fingerprint": import_run.license_fingerprint,
                    "accepted": import_run.accepted_license,
                },
            }
            version.error_code = ""
            version.error_message = ""
            version.available_at = now
            version.save(
                update_fields=(
                    "status",
                    "checksum_sha256",
                    "size_bytes",
                    "manifest",
                    "error_code",
                    "error_message",
                    "available_at",
                    "updated_at",
                )
            )

            dataset.size_bytes = stored_object.size_bytes
            self._refresh_dataset_metadata(dataset, source_dataset)

            import_run.status = DatasetImportStatus.SUCCEEDED
            import_run.dataset = dataset
            import_run.dataset_version = version
            import_run.error_code = ""
            import_run.error_message = ""
            import_run.finished_at = now
            import_run.save(
                update_fields=(
                    "status",
                    "dataset",
                    "dataset_version",
                    "error_code",
                    "error_message",
                    "finished_at",
                    "updated_at",
                )
            )

        return ImportExecutionResult(
            import_id=import_run.pk,
            dataset_id=dataset.pk,
            dataset_version_id=version.pk,
            artifact_id=artifact.pk,
            checksum_sha256=artifact.checksum_sha256,
            size_bytes=artifact.size_bytes,
            object_reused=stored_object.reused,
            version_reused=False,
        )

    def _assert_policy_still_allows(
        self,
        source_dataset: SourceDataset,
        context: ImportExecutionContext,
    ) -> None:
        current_revision = source_dataset_revision(source_dataset)

        if current_revision != context.source_revision:
            raise DatasetImportSourceChanged(
                "The remote dataset revision changed during download."
            )

        import_run = DatasetImport.objects.only(
            "accepted_license",
            "license_fingerprint",
            "policy_snapshot",
        ).get(pk=context.import_id)
        decision = self._policy.evaluate(
            source_dataset,
            accepted_license=import_run.accepted_license,
            presented_license_fingerprint=(
                import_run.license_fingerprint
            ),
            private_access_authorized=bool(
                import_run.policy_snapshot.get(
                    "private_access_authorized",
                    False,
                )
            ),
        )

        if not decision.allowed:
            raise DatasetImportPolicyRejected(decision)

    def _finish_with_error(
        self,
        import_id: UUID | str,
        *,
        status: str,
        error_code: str,
        error_message: str,
    ) -> None:
        now = timezone.now()

        with transaction.atomic():
            try:
                import_run = (
                    DatasetImport.objects.select_for_update()
                    .select_related("dataset_version")
                    .get(pk=import_id)
                )
            except DatasetImport.DoesNotExist:
                return

            if import_run.status == DatasetImportStatus.SUCCEEDED:
                return

            import_run.status = status
            import_run.error_code = error_code[:64]
            import_run.error_message = error_message
            import_run.finished_at = now
            import_run.save(
                update_fields=(
                    "status",
                    "error_code",
                    "error_message",
                    "finished_at",
                    "updated_at",
                )
            )

            version = import_run.dataset_version

            if (
                version is not None
                and version.status != DatasetVersionStatus.AVAILABLE
            ):
                version.status = DatasetVersionStatus.FAILED
                version.error_code = error_code[:64]
                version.error_message = error_message
                version.save(
                    update_fields=(
                        "status",
                        "error_code",
                        "error_message",
                        "updated_at",
                    )
                )

    @classmethod
    def _dataset_defaults(
        cls,
        source_dataset: SourceDataset,
    ) -> dict[str, object]:
        return {
            "origin": DatasetOrigin.IMPORTED,
            "visibility": settings.DATASET_IMPORTED_VISIBILITY,
            "title": source_dataset.title,
            "description": (
                source_dataset.description or source_dataset.subtitle
            ),
            "source_url": source_dataset.source_url,
            "license": source_dataset.license_name,
            "license_names": cls._source_license_names(source_dataset),
            "size_bytes": source_dataset.total_bytes,
        }

    def _refresh_dataset_metadata(
        self,
        dataset: Dataset,
        source_dataset: SourceDataset,
    ) -> None:
        dataset.origin = DatasetOrigin.IMPORTED
        dataset.title = source_dataset.title
        dataset.description = (
            source_dataset.description or source_dataset.subtitle
        )
        dataset.source_url = source_dataset.source_url
        dataset.license = source_dataset.license_name
        dataset.license_names = self._source_license_names(
            source_dataset
        )

        if dataset.size_bytes is None:
            dataset.size_bytes = source_dataset.total_bytes

        dataset.save()


    @staticmethod
    def _source_license_names(
        source_dataset: SourceDataset,
    ) -> list[str]:
        names = [
            str(value).strip()
            for value in (source_dataset.license_names or [])
            if str(value).strip()
        ]

        if not names and source_dataset.license_name.strip():
            names.append(source_dataset.license_name.strip())

        return list(dict.fromkeys(names))

    @staticmethod
    def _hash_file(
        path: Path,
        *,
        maximum_bytes: int,
    ) -> tuple[str, int]:
        digest = hashlib.sha256()
        total = 0

        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)

                    if total > maximum_bytes:
                        raise DatasetImportTooLarge(
                            "The downloaded artifact exceeds the configured "
                            "import limit."
                        )

                    digest.update(chunk)
        except OSError as exc:
            raise DatasetImportError(
                "The downloaded artifact could not be read."
            ) from exc

        return digest.hexdigest(), total

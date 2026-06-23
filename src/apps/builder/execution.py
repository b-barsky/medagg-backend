from __future__ import annotations

import hashlib
import logging
import mimetypes
import tempfile
from contextlib import ExitStack
from pathlib import Path

import duckdb
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.datasets.models import (
    Dataset,
    DatasetArtifact,
    DatasetArtifactKind,
    DatasetMembership,
    DatasetMembershipAcquisition,
    DatasetOrigin,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibility,
)
from apps.datasets.storage import ObjectStorageError, S3ObjectStorage

from .artifacts import ArtifactMaterializer
from .models import (
    BuildInput,
    BuildRequest,
    BuildRequestStatus,
    BuildRun,
    BuildRunStatus,
    DatasetFieldSchema,
    DatasetLineage,
    DatasetTableSchema,
    PrivacyAssessmentStatus,
    TransformationPlanStatus,
)
from .schema import canonical_json, quote_identifier, quote_literal, reader_expression


logger = logging.getLogger(__name__)


class BuildExecutionError(RuntimeError):
    code = "build_failed"


class BuildExecutionRejected(BuildExecutionError):
    code = "build_rejected"


class BuildTransientError(BuildExecutionError):
    code = "transient_build_failure"


class BuildExecutionService:
    def __init__(
        self,
        *,
        storage: S3ObjectStorage | None = None,
        materializer_factory=ArtifactMaterializer,
    ) -> None:
        self.storage = storage or S3ObjectStorage()
        self.materializer_factory = materializer_factory

    def create_run(self, request: BuildRequest, user) -> tuple[BuildRun, bool]:
        if request.user_id != user.pk:
            raise BuildExecutionRejected("You cannot execute another user's plan.")
        if request.status != BuildRequestStatus.READY:
            raise BuildExecutionRejected("The build request does not have a ready plan.")
        plan = request.plans.filter(status=TransformationPlanStatus.ACTIVE).first()
        if plan is None:
            raise BuildExecutionRejected("The active transformation plan is missing.")
        assessment = getattr(plan, "privacy_assessment", None)
        if assessment is None or assessment.status != PrivacyAssessmentStatus.APPROVED:
            raise BuildExecutionRejected("The plan has not passed privacy assessment.")
        with transaction.atomic():
            active = (
                BuildRun.objects.select_for_update()
                .filter(request=request, status__in=BuildRun.ACTIVE_STATUSES)
                .first()
            )
            if active is not None:
                return active, False
            run = BuildRun.objects.create(
                request=request,
                plan=plan,
                requested_by=user,
                status=BuildRunStatus.QUEUED,
                progress=0,
                progress_message="Queued",
            )
            request.status = BuildRequestStatus.BUILDING
            request.progress = 0
            request.progress_message = "Build queued"
            request.finished_at = None
            request.save()
        return run, True

    def execute(self, run_id) -> BuildRun:
        run = BuildRun.objects.select_related(
            "request",
            "plan",
            "requested_by",
            "plan__privacy_assessment",
            "output_dataset",
            "output_version",
        ).get(pk=run_id)
        if run.status == BuildRunStatus.SUCCEEDED:
            return run
        self._validate_run(run)
        BuildRun.objects.filter(pk=run.pk).update(
            status=BuildRunStatus.RUNNING,
            progress=5,
            progress_message="Revalidating authorization and lineage",
            attempt_count=run.attempt_count + 1,
            started_at=run.started_at or timezone.now(),
            last_attempt_at=timezone.now(),
            error_code="",
            error_message="",
            updated_at=timezone.now(),
        )
        plan_payload = run.plan.plan
        input_records = self._revalidate_inputs(run, plan_payload)
        with tempfile.TemporaryDirectory(prefix="medagg-build-output-") as directory:
            output = Path(directory) / f"derived-{run.pk}.parquet"
            self._set_progress(run.pk, 15, "Materializing authorized inputs")
            row_count = self._build_parquet(run, input_records, output)
            size_bytes = output.stat().st_size
            if size_bytes > settings.BUILDER_MAX_OUTPUT_BYTES:
                raise BuildExecutionRejected(
                    "The derived artifact exceeds the configured output-size limit."
                )
            if row_count > settings.BUILDER_MAX_OUTPUT_ROWS:
                raise BuildExecutionRejected(
                    "The derived dataset exceeds the configured row limit."
                )
            checksum = file_sha256(output)
            self._set_progress(run.pk, 80, "Uploading immutable derived artifact")
            output_dataset, output_version = self._create_output_records(
                run,
                input_records,
                row_count,
                size_bytes,
                checksum,
            )
            try:
                stored = self.storage.store_file(
                    output,
                    dataset_id=output_dataset.pk,
                    filename=output.name,
                    content_type=(
                        mimetypes.guess_type(output.name)[0]
                        or "application/vnd.apache.parquet"
                    ),
                    size_bytes=size_bytes,
                    checksum_sha256=checksum,
                )
            except ObjectStorageError as exc:
                DatasetVersion.objects.filter(pk=output_version.pk).update(
                    status=DatasetVersionStatus.FAILED,
                    error_code="object_storage_failed",
                    error_message=str(exc),
                )
                raise BuildTransientError(str(exc)) from exc
            self._finish_success(
                run,
                input_records,
                output_dataset,
                output_version,
                stored,
                row_count,
                size_bytes,
                checksum,
            )
        return BuildRun.objects.get(pk=run.pk)

    def _validate_run(self, run: BuildRun) -> None:
        if run.status == BuildRunStatus.SUCCEEDED:
            return
        if run.plan.status != TransformationPlanStatus.ACTIVE:
            raise BuildExecutionRejected("The transformation plan is no longer active.")
        actual = hashlib.sha256(canonical_json(run.plan.plan)).hexdigest()
        if actual != run.plan.plan_checksum:
            raise BuildExecutionRejected("The transformation plan checksum is invalid.")
        if run.plan.privacy_assessment.status != PrivacyAssessmentStatus.APPROVED:
            raise BuildExecutionRejected("Privacy assessment no longer permits this plan.")

    def _revalidate_inputs(self, run, payload):
        inputs: list[tuple[DatasetVersion, DatasetTableSchema, DatasetFieldSchema, dict]] = []
        for item in payload["inputs"]:
            version = DatasetVersion.objects.select_related("dataset").get(
                pk=item["dataset_version_id"]
            )
            if version.status != DatasetVersionStatus.AVAILABLE:
                raise BuildExecutionRejected("An input version is no longer available.")
            if not DatasetMembership.objects.filter(
                user=run.requested_by,
                dataset=version.dataset,
            ).exists():
                raise BuildExecutionRejected(
                    "Access to an input dataset was revoked before execution."
                )
            analysis = version.builder_analysis
            if analysis.schema_fingerprint != item["analysis_schema_fingerprint"]:
                raise BuildExecutionRejected(
                    "An input schema changed after the plan was created."
                )
            table = DatasetTableSchema.objects.get(
                pk=item["table_id"], analysis=analysis
            )
            join_field = DatasetFieldSchema.objects.get(
                pk=item["join_field"]["id"],
                table=table,
                join_candidate=True,
            )
            if join_field.semantic_type != payload["join"]["semantic_type"]:
                raise BuildExecutionRejected("The linkage field no longer matches the plan.")
            inputs.append((version, table, join_field, item))
        if len(inputs) < 2:
            raise BuildExecutionRejected("At least two valid inputs are required.")
        return inputs

    def _build_parquet(self, run, inputs, output: Path) -> int:
        connection = duckdb.connect(database=":memory:")
        connection.execute(f"SET threads={settings.BUILDER_DUCKDB_THREADS}")
        connection.execute(
            f"SET memory_limit='{settings.BUILDER_DUCKDB_MEMORY_LIMIT_MB}MB'"
        )
        with ExitStack() as stack:
            aliases: list[tuple[str, DatasetFieldSchema, dict]] = []
            for index, (version, table, join_field, item) in enumerate(inputs, start=1):
                materialized = stack.enter_context(self.materializer_factory(version))
                table_path = (materialized.root / table.relative_path).resolve()
                try:
                    table_path.relative_to(materialized.root.resolve())
                except ValueError as exc:
                    raise BuildExecutionRejected("A planned table path is invalid.") from exc
                if not table_path.is_file():
                    raise BuildExecutionRejected(
                        f"Planned table '{table.relative_path}' is missing from its artifact."
                    )
                view = f"input_{index}"
                connection.execute(
                    f"CREATE VIEW {quote_identifier(view)} AS SELECT * FROM "
                    f"{reader_expression(table_path, table.format)}"
                )
                aliases.append((view, join_field, item))
            self._set_progress(run.pk, 45, "Executing exact pseudonymous join")
            from_sql = f"{quote_identifier(aliases[0][0])} AS t1"
            base_field = quote_identifier(aliases[0][1].name)
            for index, (view, join_field, _) in enumerate(aliases[1:], start=2):
                from_sql += (
                    f" INNER JOIN {quote_identifier(view)} AS t{index} ON "
                    f"CAST(t1.{base_field} AS VARCHAR) = "
                    f"CAST(t{index}.{quote_identifier(join_field.name)} AS VARCHAR)"
                )
            projections = ["row_number() OVER () AS medagg_row_id"]
            for index, (_, join_field, item) in enumerate(aliases, start=1):
                for field in item["fields"]:
                    if not field["include_in_output"]:
                        continue
                    source = quote_identifier(field["name"])
                    alias = quote_identifier(f"d{index}__{field['name']}")
                    projections.append(f"t{index}.{source} AS {alias}")
            if len(projections) == 1:
                raise BuildExecutionRejected(
                    "All available output fields were excluded by privacy policy."
                )
            select_sql = f"SELECT {', '.join(projections)} FROM {from_sql}"
            connection.execute(
                f"COPY ({select_sql}) TO {quote_literal(str(output))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            row_count = int(
                connection.execute(
                    f"SELECT count(*) FROM read_parquet({quote_literal(str(output))})"
                ).fetchone()[0]
            )
        connection.close()
        if row_count == 0:
            raise BuildExecutionRejected(
                "The selected datasets have no rows with matching linkage identifiers."
            )
        return row_count

    @staticmethod
    def _create_output_records(run, inputs, row_count, size_bytes, checksum):
        """Create or reuse the staging output for an idempotent build attempt."""

        license_names = sorted(
            {
                name
                for version, _, _, _ in inputs
                for name in (version.dataset.license_names or [])
            }
        )
        manifest = {
            "kind": "derived",
            "plan_id": str(run.plan_id),
            "plan_checksum": run.plan.plan_checksum,
            "input_versions": [str(item[0].pk) for item in inputs],
            "privacy_rules_version": run.plan.privacy_assessment.rules_version,
        }
        legacy_metadata = {
            "builder_request_id": str(run.request_id),
            "transformation_plan_id": str(run.plan_id),
        }

        with transaction.atomic():
            # Lock only the BuildRun row first. output_dataset and
            # output_version are nullable foreign keys, so combining
            # select_for_update() with select_related() would generate
            # LEFT OUTER JOINs that PostgreSQL cannot lock.
            locked_run = BuildRun.objects.select_for_update().get(pk=run.pk)

            if bool(locked_run.output_dataset_id) != bool(
                    locked_run.output_version_id
            ):
                raise BuildExecutionError(
                    "The build output references are inconsistent."
                )

            if locked_run.output_dataset_id is not None:
                # Lock the reusable staging rows explicitly in separate
                # queries after the BuildRun lock has established the
                # output identity for this retry.
                dataset = Dataset.objects.select_for_update().get(
                    pk=locked_run.output_dataset_id,
                )
                version = DatasetVersion.objects.select_for_update().get(
                    pk=locked_run.output_version_id,
                    dataset_id=dataset.pk,
                )
                dataset.title = f"Derived dataset: {run.request.prompt[:180]}"
                dataset.description = (
                    "AI-planned exact join produced by Medagg. "
                    f"Purpose: {run.request.purpose}"
                )
                dataset.license = "Derived output; input terms continue to apply"
                dataset.license_names = license_names
                dataset.record_count = row_count
                dataset.size_bytes = size_bytes
                dataset.legacy_metadata = {
                    **(dataset.legacy_metadata or {}),
                    **legacy_metadata,
                }
                dataset.save()

                version.status = DatasetVersionStatus.STAGING
                version.source_revision = run.plan.plan_checksum
                version.source_version = f"plan-v{run.plan.version}"
                version.checksum_sha256 = checksum
                version.size_bytes = size_bytes
                version.record_count = row_count
                version.manifest = manifest
                version.error_code = ""
                version.error_message = ""
                version.available_at = None
                version.save()
                return dataset, version

            dataset = Dataset.objects.create(
                origin=DatasetOrigin.DERIVED,
                visibility=DatasetVisibility.PRIVATE,
                title=f"Derived dataset: {run.request.prompt[:180]}",
                description=(
                    "AI-planned exact join produced by Medagg. "
                    f"Purpose: {run.request.purpose}"
                ),
                license="Derived output; input terms continue to apply",
                license_names=license_names,
                record_count=row_count,
                size_bytes=size_bytes,
                legacy_metadata=legacy_metadata,
            )
            version = DatasetVersion.objects.create(
                dataset=dataset,
                number=1,
                status=DatasetVersionStatus.STAGING,
                source_revision=run.plan.plan_checksum,
                source_version=f"plan-v{run.plan.version}",
                checksum_sha256=checksum,
                size_bytes=size_bytes,
                record_count=row_count,
                manifest=manifest,
            )
            locked_run.output_dataset = dataset
            locked_run.output_version = version
            locked_run.save(
                update_fields=(
                    "output_dataset",
                    "output_version",
                    "updated_at",
                )
            )

        return dataset, version

    @staticmethod
    def _finish_success(
        run,
        inputs,
        dataset,
        version,
        stored,
        row_count,
        size_bytes,
        checksum,
    ) -> None:
        with transaction.atomic():
            locked_run = BuildRun.objects.select_for_update().get(pk=run.pk)
            if locked_run.status == BuildRunStatus.SUCCEEDED:
                return

            artifact = DatasetArtifact.objects.filter(
                dataset_version=version,
                kind=DatasetArtifactKind.DATA,
            ).first()
            if artifact is None:
                artifact = DatasetArtifact(
                    dataset_version=version,
                    kind=DatasetArtifactKind.DATA,
                )
            artifact.storage_backend = "s3"
            artifact.bucket = stored.bucket
            artifact.object_key = stored.object_key
            artifact.object_version_id = stored.object_version_id
            artifact.filename = f"derived-{run.pk}.parquet"
            artifact.content_type = "application/vnd.apache.parquet"
            artifact.size_bytes = size_bytes
            artifact.checksum_sha256 = checksum
            artifact.etag = stored.etag
            artifact.save()

            version.status = DatasetVersionStatus.AVAILABLE
            version.error_code = ""
            version.error_message = ""
            version.available_at = timezone.now()
            version.save()
            DatasetMembership.objects.get_or_create(
                user=run.requested_by,
                dataset=dataset,
                defaults={
                    "acquisition": DatasetMembershipAcquisition.DERIVED,
                },
            )
            for position, (input_version, table, join_field, _) in enumerate(
                inputs, start=1
            ):
                BuildInput.objects.update_or_create(
                    run=locked_run,
                    dataset_version=input_version,
                    defaults={
                        "table_schema": table,
                        "join_field": join_field,
                        "position": position,
                        "schema_fingerprint": table.schema_fingerprint,
                    },
                )
                DatasetLineage.objects.get_or_create(
                    output_version=version,
                    input_version=input_version,
                    defaults={
                        "build_run": locked_run,
                        "transformation_plan": run.plan,
                    },
                )
                dataset.modalities.add(*input_version.dataset.modalities.all())
                dataset.ml_tasks.add(*input_version.dataset.ml_tasks.all())
                dataset.tags.add(*input_version.dataset.tags.all())
                if dataset.anatomical_area_id is None:
                    dataset.anatomical_area = input_version.dataset.anatomical_area
            dataset.save()
            BuildRun.objects.filter(pk=locked_run.pk).update(
                output_dataset=dataset,
                output_version=version,
                status=BuildRunStatus.SUCCEEDED,
                progress=100,
                progress_message="Derived dataset is ready",
                output_row_count=row_count,
                output_checksum_sha256=checksum,
                error_code="",
                error_message="",
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            BuildRequest.objects.filter(pk=run.request_id).update(
                status=BuildRequestStatus.SUCCEEDED,
                progress=100,
                progress_message="Derived dataset is ready",
                error_code="",
                error_message="",
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
        logger.info(
            "builder.run.succeeded",
            extra={
                "event": "builder.run.succeeded",
                "build_run_id": str(run.pk),
                "output_dataset_id": dataset.pk,
                "output_version_id": str(version.pk),
                "row_count": row_count,
            },
        )

    @staticmethod
    def _set_progress(run_id, progress, message):
        BuildRun.objects.filter(pk=run_id).update(
            progress=progress,
            progress_message=message,
            updated_at=timezone.now(),
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

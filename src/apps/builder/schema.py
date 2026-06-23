from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import duckdb
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.datasets.models import (
    AnatomicalArea,
    Dataset,
    DatasetVersion,
    DatasetVersionStatus,
    MLTask,
    Modality,
    Tag,
)

from .artifacts import ArtifactMaterializationError, ArtifactMaterializer
from .ml import BuilderModelService
from .models import (
    AnalysisStatus,
    DatasetAnalysis,
    DatasetFieldSchema,
    DatasetTableSchema,
    DatasetTagPrediction,
    PredictionNamespace,
    SemanticFieldType,
)
from .privacy import classify_field_privacy


logger = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".parquet": "parquet",
    ".json": "json",
    ".jsonl": "json",
    ".ndjson": "json",
    ".csv.gz": "csv",
    ".json.gz": "json",
    ".jsonl.gz": "json",
}


class SchemaDiscoveryError(RuntimeError):
    pass


class UnsupportedDatasetError(SchemaDiscoveryError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveredField:
    ordinal: int
    name: str
    physical_type: str
    nullable: bool
    semantic_type: str
    semantic_confidence: float
    privacy_class: str
    join_candidate: bool
    non_null_count: int
    distinct_count: int | None
    null_fraction: float | None
    unique_ratio: float | None
    value_profile: dict[str, object]


@dataclass(frozen=True, slots=True)
class DiscoveredTable:
    relative_path: str
    format: str
    logical_name: str
    row_count: int
    file_size_bytes: int
    schema_fingerprint: str
    fields: tuple[DiscoveredField, ...]


class DatasetSchemaAnalyzer:
    def __init__(
        self,
        *,
        materializer_factory=ArtifactMaterializer,
        model_service: BuilderModelService | None = None,
    ) -> None:
        self.materializer_factory = materializer_factory
        self.model_service = model_service or BuilderModelService()

    def analyze(self, dataset_version_id) -> DatasetAnalysis:
        version = DatasetVersion.objects.select_related(
            "dataset", "dataset__source_dataset"
        ).get(pk=dataset_version_id)
        if version.status != DatasetVersionStatus.AVAILABLE:
            raise SchemaDiscoveryError("Only available dataset versions can be analyzed.")
        analysis, _ = DatasetAnalysis.objects.get_or_create(
            dataset_version=version,
            defaults={"status": AnalysisStatus.PENDING},
        )
        self._set_progress(analysis.pk, AnalysisStatus.RUNNING, 5, "Loading AI model")
        release, model = self.model_service.load_active()
        try:
            with self.materializer_factory(version) as materialized:
                self._set_progress(
                    analysis.pk,
                    AnalysisStatus.RUNNING,
                    15,
                    "Inspecting tabular files",
                )
                candidates = self._candidate_files(materialized.root, materialized.files)
                if not candidates:
                    raise UnsupportedDatasetError(
                        "No supported CSV, TSV, Parquet, JSON or JSONL table was found."
                    )
                tables = self._discover_tables(
                    materialized.root,
                    candidates,
                    model,
                    analysis.pk,
                )
        except UnsupportedDatasetError as exc:
            return self._finish_error(
                analysis.pk,
                AnalysisStatus.UNSUPPORTED,
                "unsupported_dataset",
                str(exc),
            )
        except (ArtifactMaterializationError, SchemaDiscoveryError, duckdb.Error) as exc:
            return self._finish_error(
                analysis.pk,
                AnalysisStatus.FAILED,
                "schema_discovery_failed",
                str(exc),
            )

        dataset_text = self._dataset_text(version.dataset, tables)
        predictions = model.predict_dataset_tags(dataset_text)
        schema_fingerprint = self._schema_fingerprint(tables)
        summary = {
            "table_count": len(tables),
            "field_count": sum(len(table.fields) for table in tables),
            "join_candidate_count": sum(
                1
                for table in tables
                for field in table.fields
                if field.join_candidate
            ),
            "supported_formats": sorted({table.format for table in tables}),
            "model_version": release.version,
            "raw_values_persisted": False,
        }
        with transaction.atomic():
            locked = DatasetAnalysis.objects.select_for_update().get(pk=analysis.pk)
            locked.tables.all().delete()
            locked.tag_predictions.all().delete()
            locked.model_release = release
            locked.schema_fingerprint = schema_fingerprint
            locked.dataset_text_checksum = hashlib.sha256(
                dataset_text.encode("utf-8")
            ).hexdigest()
            locked.summary = summary
            locked.status = AnalysisStatus.COMPLETE
            locked.progress = 100
            locked.progress_message = "Analysis complete"
            locked.error_code = ""
            locked.error_message = ""
            locked.finished_at = timezone.now()
            locked.save()
            for table in tables:
                table_record = DatasetTableSchema.objects.create(
                    analysis=locked,
                    relative_path=table.relative_path,
                    format=table.format,
                    logical_name=table.logical_name,
                    row_count=table.row_count,
                    column_count=len(table.fields),
                    file_size_bytes=table.file_size_bytes,
                    schema_fingerprint=table.schema_fingerprint,
                    metadata={"reader": "duckdb"},
                )
                DatasetFieldSchema.objects.bulk_create(
                    [
                        DatasetFieldSchema(
                            table=table_record,
                            ordinal=field.ordinal,
                            name=field.name,
                            physical_type=field.physical_type,
                            nullable=field.nullable,
                            semantic_type=field.semantic_type,
                            semantic_confidence=Decimal(
                                f"{field.semantic_confidence:.5f}"
                            ),
                            privacy_class=field.privacy_class,
                            join_candidate=field.join_candidate,
                            non_null_count=field.non_null_count,
                            distinct_count=field.distinct_count,
                            null_fraction=(
                                Decimal(f"{field.null_fraction:.6f}")
                                if field.null_fraction is not None
                                else None
                            ),
                            unique_ratio=(
                                Decimal(f"{field.unique_ratio:.6f}")
                                if field.unique_ratio is not None
                                else None
                            ),
                            value_profile=field.value_profile,
                        )
                        for field in table.fields
                    ]
                )
            prediction_records = [
                DatasetTagPrediction(
                    analysis=locked,
                    namespace=item["namespace"],
                    value=item["value"],
                    confidence=Decimal(f"{float(item['confidence']):.5f}"),
                    evidence=[f"model:{item['label']}"],
                )
                for item in predictions
            ]
            DatasetTagPrediction.objects.bulk_create(prediction_records)
            self._apply_predictions(version.dataset, locked)
        logger.info(
            "builder.analysis.completed",
            extra={
                "event": "builder.analysis.completed",
                "analysis_id": str(analysis.pk),
                "dataset_version_id": str(version.pk),
                "table_count": len(tables),
            },
        )
        return DatasetAnalysis.objects.get(pk=analysis.pk)

    @staticmethod
    def _candidate_files(root: Path, files: Iterable[Path]) -> list[tuple[Path, str]]:
        candidates: list[tuple[Path, str]] = []
        for path in files:
            relative = str(path.relative_to(root)).replace("\\", "/")
            lowered = relative.casefold()
            detected = next(
                (value for suffix, value in SUPPORTED_SUFFIXES.items() if lowered.endswith(suffix)),
                None,
            )
            if detected:
                candidates.append((path, detected))
        candidates.sort(key=lambda item: (item[0].stat().st_size, str(item[0])), reverse=True)
        return candidates[: settings.BUILDER_MAX_TABLE_FILES]

    def _discover_tables(self, root, candidates, model, analysis_id):
        tables: list[DiscoveredTable] = []
        connection = duckdb.connect(database=":memory:")
        connection.execute(f"SET threads={settings.BUILDER_DUCKDB_THREADS}")
        connection.execute(
            f"SET memory_limit='{settings.BUILDER_DUCKDB_MEMORY_LIMIT_MB}MB'"
        )
        try:
            for index, (path, format_name) in enumerate(candidates, start=1):
                progress = 15 + int(65 * index / max(len(candidates), 1))
                self._set_progress(
                    analysis_id,
                    AnalysisStatus.RUNNING,
                    progress,
                    f"Analyzing {path.name}",
                )
                try:
                    table = self._discover_table(
                        connection,
                        root,
                        path,
                        format_name,
                        model,
                    )
                except duckdb.Error as exc:
                    logger.warning(
                        "builder.analysis.table_skipped",
                        extra={
                            "event": "builder.analysis.table_skipped",
                            "path": str(path),
                            "error": str(exc),
                        },
                    )
                    continue
                if table.fields:
                    tables.append(table)
        finally:
            connection.close()
        if not tables:
            raise UnsupportedDatasetError(
                "Supported files were found but none could be parsed as a table."
            )
        return tables

    def _discover_table(self, connection, root, path, format_name, model):
        reader = reader_expression(path, format_name, ignore_errors=True)
        description = connection.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()
        row_count = int(connection.execute(f"SELECT count(*) FROM {reader}").fetchone()[0])
        fields: list[DiscoveredField] = []
        for ordinal, row in enumerate(description):
            name = str(row[0])
            physical_type = str(row[1])
            nullable = str(row[2]).upper() != "NO"
            quoted = quote_identifier(name)
            counts = connection.execute(
                f"SELECT count({quoted}), approx_count_distinct({quoted}) FROM {reader}"
            ).fetchone()
            non_null = int(counts[0] or 0)
            distinct = int(counts[1] or 0) if counts[1] is not None else None
            sample_rows = connection.execute(
                f"SELECT CAST({quoted} AS VARCHAR) FROM {reader} "
                f"WHERE {quoted} IS NOT NULL LIMIT {settings.BUILDER_PROFILE_SAMPLE_ROWS}"
            ).fetchall()
            profile = value_profile([str(item[0]) for item in sample_rows])
            semantic_type, confidence = model.predict_field(
                name=name,
                physical_type=physical_type,
                value_profile=profile,
            )
            semantic_type, confidence = semantic_rule_override(
                name=name,
                semantic_type=semantic_type,
                confidence=confidence,
            )
            null_fraction = (
                (row_count - non_null) / row_count if row_count > 0 else None
            )
            unique_ratio = (
                min(1.0, distinct / non_null)
                if distinct is not None and non_null > 0
                else None
            )
            privacy = classify_field_privacy(
                name=name,
                semantic_type=semantic_type,
                semantic_confidence=confidence,
                unique_ratio=unique_ratio,
            )
            fields.append(
                DiscoveredField(
                    ordinal=ordinal,
                    name=name,
                    physical_type=physical_type,
                    nullable=nullable,
                    semantic_type=semantic_type,
                    semantic_confidence=confidence,
                    privacy_class=privacy.privacy_class,
                    join_candidate=privacy.join_candidate,
                    non_null_count=non_null,
                    distinct_count=distinct,
                    null_fraction=null_fraction,
                    unique_ratio=unique_ratio,
                    value_profile=profile,
                )
            )
        relative = str(path.relative_to(root)).replace("\\", "/")
        fingerprint_payload = [
            {
                "name": field.name,
                "type": field.physical_type,
                "semantic": field.semantic_type,
                "privacy": field.privacy_class,
            }
            for field in fields
        ]
        fingerprint = hashlib.sha256(
            canonical_json(fingerprint_payload)
        ).hexdigest()
        return DiscoveredTable(
            relative_path=relative,
            format=format_name,
            logical_name=Path(relative).stem[:255],
            row_count=row_count,
            file_size_bytes=path.stat().st_size,
            schema_fingerprint=fingerprint,
            fields=tuple(fields),
        )

    @staticmethod
    def _dataset_text(dataset: Dataset, tables: list[DiscoveredTable]) -> str:
        source = dataset.source_dataset
        metadata_parts: list[str] = []
        if source is not None:
            for field in ("title", "subtitle", "description", "owner_name"):
                value = getattr(source, field, "")
                if value:
                    metadata_parts.append(str(value))
            detail = getattr(source, "detail_metadata", {}) or {}
            metadata_parts.append(json.dumps(detail, ensure_ascii=False)[:12000])
        schema_terms = " ".join(
            [table.logical_name for table in tables]
            + [field.name for table in tables for field in table.fields]
        )
        return "\n".join(
            [dataset.title, dataset.description, schema_terms, *metadata_parts]
        )[:30000]

    @staticmethod
    def _schema_fingerprint(tables: list[DiscoveredTable]) -> str:
        payload = [
            {
                "path": table.relative_path,
                "format": table.format,
                "fingerprint": table.schema_fingerprint,
            }
            for table in tables
        ]
        return hashlib.sha256(canonical_json(payload)).hexdigest()

    @staticmethod
    def _apply_predictions(dataset: Dataset, analysis: DatasetAnalysis) -> None:
        predictions = list(analysis.tag_predictions.order_by("-confidence"))
        area = next(
            (
                item
                for item in predictions
                if item.namespace == PredictionNamespace.ANATOMICAL_AREA
            ),
            None,
        )
        if area is not None and dataset.anatomical_area_id is None:
            dataset.anatomical_area, _ = AnatomicalArea.objects.get_or_create(
                name=area.value
            )
            dataset.save(update_fields=("anatomical_area", "updated_at"))
            area.applied = True
            area.save(update_fields=("applied",))
        for prediction in predictions:
            if prediction.namespace == PredictionNamespace.MODALITY:
                value, _ = Modality.objects.get_or_create(name=prediction.value)
                dataset.modalities.add(value)
                prediction.applied = True
            elif prediction.namespace == PredictionNamespace.ML_TASK:
                value, _ = MLTask.objects.get_or_create(name=prediction.value)
                dataset.ml_tasks.add(value)
                prediction.applied = True
            elif prediction.namespace == PredictionNamespace.TAG:
                value, _ = Tag.objects.get_or_create(name=prediction.value)
                dataset.tags.add(value)
                prediction.applied = True
            if prediction.applied:
                prediction.save(update_fields=("applied",))

    @staticmethod
    def _set_progress(analysis_id, status, progress, message) -> None:
        now = timezone.now()
        if status == AnalysisStatus.RUNNING:
            DatasetAnalysis.objects.filter(
                pk=analysis_id,
                started_at__isnull=True,
            ).update(started_at=now)
        DatasetAnalysis.objects.filter(pk=analysis_id).update(
            status=status,
            progress=min(100, max(0, progress)),
            progress_message=message,
            updated_at=now,
        )

    @staticmethod
    def _finish_error(analysis_id, status, code, message):
        DatasetAnalysis.objects.filter(pk=analysis_id).update(
            status=status,
            progress=100,
            progress_message=message[:255],
            error_code=code,
            error_message=message,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return DatasetAnalysis.objects.get(pk=analysis_id)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def reader_expression(
    path: Path,
    format_name: str,
    *,
    ignore_errors: bool = False,
) -> str:
    literal = quote_literal(str(path.resolve()))
    permissive = ", ignore_errors=true" if ignore_errors else ""
    if format_name == "parquet":
        return f"read_parquet({literal})"
    if format_name == "csv":
        return (
            f"read_csv_auto({literal}, header=true, sample_size=100000"
            f"{permissive})"
        )
    if format_name == "tsv":
        return (
            f"read_csv_auto({literal}, header=true, delim='\t', "
            f"sample_size=100000{permissive})"
        )
    if format_name == "json":
        return f"read_json_auto({literal})"
    raise UnsupportedDatasetError(f"Unsupported table format: {format_name}")


def value_profile(values: list[str]) -> dict[str, object]:
    if not values:
        return {"sample_count": 0}
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    email_pattern = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    numeric = 0
    dates = 0
    uuid_count = 0
    email_count = 0
    lengths: list[int] = []
    for value in values:
        stripped = value.strip()
        lengths.append(len(stripped))
        try:
            float(stripped)
            numeric += 1
        except ValueError:
            pass
        try:
            datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            dates += 1
        except ValueError:
            pass
        uuid_count += int(bool(uuid_pattern.match(stripped)))
        email_count += int(bool(email_pattern.match(stripped)))
    total = len(values)
    return {
        "sample_count": total,
        "average_length": round(sum(lengths) / total, 3),
        "maximum_length": max(lengths),
        "numeric_fraction": round(numeric / total, 4),
        "date_fraction": round(dates / total, 4),
        "uuid_fraction": round(uuid_count / total, 4),
        "email_fraction": round(email_count / total, 4),
    }


def semantic_rule_override(
    *,
    name: str,
    semantic_type: str,
    confidence: float,
) -> tuple[str, float]:
    normalized = re.sub(r"[^a-zа-я0-9]+", "_", name.casefold()).strip("_")
    rules = (
        (r"(^|_)patient(_|$)", SemanticFieldType.PATIENT_ID, "id"),
        (r"(^|_)subject(_|$)|participant", SemanticFieldType.SUBJECT_ID, "id"),
        (r"(^|_)study(_|$)", SemanticFieldType.STUDY_ID, "id"),
        (r"encounter|visit_id|admission_id", SemanticFieldType.ENCOUNTER_ID, None),
        (r"smok|tobacco|кур", SemanticFieldType.SMOKING_STATUS, None),
        (r"diagnos|icd|диагноз", SemanticFieldType.DIAGNOSIS, None),
        (r"cancer|tumou?r|malignan|онколог|рак", SemanticFieldType.CANCER_STATUS, None),
        (r"(^|_)age(_|$)|возраст", SemanticFieldType.AGE, None),
        (r"(^|_)(sex|gender|пол)(_|$)", SemanticFieldType.SEX, None),
        (r"(^|_)(city|город)(_|$)", SemanticFieldType.CITY, None),
    )
    for pattern, label, required in rules:
        if re.search(pattern, normalized):
            if required is None or required in normalized:
                return label, max(confidence, 0.96)
    return semantic_type, confidence

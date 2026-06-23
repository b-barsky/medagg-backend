from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

import duckdb
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.builder.execution import BuildExecutionService
from apps.builder.models import (
    AnalysisStatus,
    BuilderModelRelease,
    BuildRequest,
    BuildRequestStatus,
    BuildRun,
    DatasetAnalysis,
    DatasetFieldSchema,
    DatasetTableSchema,
    PrivacyAssessment,
    TransformationPlan,
)
from apps.builder.schema import canonical_json
from apps.datasets.models import Dataset, DatasetVersion, DatasetVersionStatus


User = get_user_model()


class LocalMaterializer:
    roots = {}

    def __init__(self, version):
        self.version = version

    def __enter__(self):
        root = self.roots[str(self.version.pk)]
        return SimpleNamespace(root=root, files=tuple(root.iterdir()), artifact=None)

    def __exit__(self, exc_type, exc, traceback):
        return None


@override_settings(
    BUILDER_DUCKDB_THREADS=1,
    BUILDER_DUCKDB_MEMORY_LIMIT_MB=512,
)
class BuildExecutionQueryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="builder", password="x")
        release = BuilderModelRelease.objects.create(
            version="test-model",
            training_data_checksum="a" * 64,
            artifact_checksum="b" * 64,
            artifact=b"test",
            is_active=True,
        )
        self.request = BuildRequest.objects.create(
            user=self.user,
            prompt="Combine smoking and cancer records",
            purpose="Internal research",
            privacy_acknowledged=True,
            status=BuildRequestStatus.READY,
        )
        self.inputs = []
        plan_inputs = []
        for index, columns in enumerate(
            [
                [
                    ("patient_id", "patient_id", "pseudonymous_identifier", False),
                    ("patient_name", "free_text", "direct_identifier", False),
                    ("smoking", "smoking_status", "sensitive", True),
                ],
                [
                    ("patient_id", "patient_id", "pseudonymous_identifier", False),
                    ("diagnosis", "diagnosis", "sensitive", True),
                ],
            ],
            start=1,
        ):
            dataset = Dataset.objects.create(title=f"Input {index}")
            version = DatasetVersion.objects.create(
                dataset=dataset,
                number=1,
                status=DatasetVersionStatus.AVAILABLE,
            )
            analysis = DatasetAnalysis.objects.create(
                dataset_version=version,
                model_release=release,
                status=AnalysisStatus.COMPLETE,
                schema_fingerprint=f"analysis-{index}",
            )
            table = DatasetTableSchema.objects.create(
                analysis=analysis,
                relative_path="data.csv",
                format="csv",
                logical_name="data",
                row_count=2,
                column_count=len(columns),
                file_size_bytes=100,
                schema_fingerprint=f"table-{index}",
            )
            fields = []
            for ordinal, (name, semantic, privacy, include) in enumerate(columns):
                fields.append(
                    DatasetFieldSchema.objects.create(
                        table=table,
                        ordinal=ordinal,
                        name=name,
                        physical_type="VARCHAR",
                        semantic_type=semantic,
                        semantic_confidence=0.99,
                        privacy_class=privacy,
                        join_candidate=name == "patient_id",
                        non_null_count=2,
                        distinct_count=2,
                        unique_ratio=1,
                    )
                )
            join_field = fields[0]
            item = {
                "position": index,
                "dataset_id": dataset.pk,
                "dataset_title": dataset.title,
                "dataset_version_id": str(version.pk),
                "analysis_id": str(analysis.pk),
                "analysis_schema_fingerprint": analysis.schema_fingerprint,
                "table_id": table.pk,
                "table_path": table.relative_path,
                "table_format": table.format,
                "table_schema_fingerprint": table.schema_fingerprint,
                "join_field": {
                    "id": join_field.pk,
                    "name": join_field.name,
                    "semantic_type": "patient_id",
                    "semantic_confidence": 0.99,
                    "privacy_class": "pseudonymous_identifier",
                },
                "fields": [
                    {
                        "id": field.pk,
                        "name": field.name,
                        "physical_type": field.physical_type,
                        "semantic_type": field.semantic_type,
                        "semantic_confidence": 0.99,
                        "privacy_class": field.privacy_class,
                        "include_in_output": columns[field.ordinal][3],
                    }
                    for field in fields
                ],
            }
            plan_inputs.append(item)
            self.inputs.append((version, table, join_field, item))
        payload = {
            "schema_version": 1,
            "model_version": release.version,
            "request": {"id": str(self.request.pk)},
            "inputs": plan_inputs,
            "join": {
                "kind": "inner",
                "comparison": "exact_string",
                "semantic_type": "patient_id",
                "privacy_class": "pseudonymous_identifier",
                "raw_key_in_output": False,
            },
            "output": {"format": "parquet"},
        }
        import hashlib

        plan = TransformationPlan.objects.create(
            request=self.request,
            version=1,
            model_release=release,
            plan=payload,
            plan_checksum=hashlib.sha256(canonical_json(payload)).hexdigest(),
            created_by=self.user,
        )
        PrivacyAssessment.objects.create(
            plan=plan,
            status="approved",
            risk_level="high",
            rules_version="test",
        )
        self.run = BuildRun.objects.create(
            request=self.request,
            plan=plan,
            requested_by=self.user,
        )

    def test_exact_join_excludes_linkage_and_direct_identifier_columns(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second, TemporaryDirectory() as output_dir:
            first_root = Path(first)
            second_root = Path(second)
            (first_root / "data.csv").write_text(
                "patient_id,patient_name,smoking\np1,Alice,current\np2,Bob,never\n",
                encoding="utf-8",
            )
            (second_root / "data.csv").write_text(
                "patient_id,diagnosis\np1,lung cancer\np3,healthy\n",
                encoding="utf-8",
            )
            LocalMaterializer.roots = {
                str(self.inputs[0][0].pk): first_root,
                str(self.inputs[1][0].pk): second_root,
            }
            output = Path(output_dir) / "result.parquet"
            rows = BuildExecutionService(
                materializer_factory=LocalMaterializer
            )._build_parquet(self.run, self.inputs, output)
            self.assertEqual(rows, 1)
            columns = [
                row[0]
                for row in duckdb.connect().execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{output}')"
                ).fetchall()
            ]
            self.assertIn("medagg_row_id", columns)
            self.assertIn("d1__smoking", columns)
            self.assertIn("d2__diagnosis", columns)
            self.assertNotIn("patient_id", " ".join(columns))
            self.assertNotIn("patient_name", " ".join(columns))

    def test_staging_output_is_reused_across_retries(self):
        service = BuildExecutionService(materializer_factory=LocalMaterializer)

        first_dataset, first_version = service._create_output_records(
            self.run,
            self.inputs,
            row_count=1,
            size_bytes=512,
            checksum="c" * 64,
        )
        first_version.status = DatasetVersionStatus.FAILED
        first_version.save(update_fields=("status",))

        second_dataset, second_version = service._create_output_records(
            self.run,
            self.inputs,
            row_count=2,
            size_bytes=1024,
            checksum="d" * 64,
        )

        self.run.refresh_from_db()
        second_version.refresh_from_db()
        self.assertEqual(first_dataset.pk, second_dataset.pk)
        self.assertEqual(first_version.pk, second_version.pk)
        self.assertEqual(self.run.output_dataset_id, first_dataset.pk)
        self.assertEqual(self.run.output_version_id, first_version.pk)
        self.assertEqual(second_version.status, DatasetVersionStatus.STAGING)
        self.assertEqual(second_version.record_count, 2)
        self.assertEqual(second_version.checksum_sha256, "d" * 64)
        self.assertEqual(
            Dataset.objects.filter(origin="derived").count(),
            1,
        )

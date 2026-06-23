from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.builder.models import (
    AnalysisStatus,
    BuilderModelKind,
    BuilderModelRelease,
    BuildRequest,
    DatasetAnalysis,
    DatasetFieldSchema,
    DatasetTableSchema,
    PrivacyClass,
    SemanticFieldType,
)
from apps.builder.planning import BuildAuthorizationError, BuildPlanningService
from apps.datasets.models import (
    Dataset,
    DatasetMembership,
    DatasetVersion,
    DatasetVersionStatus,
)


User = get_user_model()


class FakeBundle:
    version = "fake-v1"

    @staticmethod
    def predict_prompt_tags(text):
        return [
            {
                "label": "area:lung",
                "namespace": "anatomical_area",
                "value": "Lung",
                "confidence": 0.9,
            }
        ]


class FakeModelService:
    def __init__(self, release):
        self.release = release

    def load_active(self):
        return self.release, FakeBundle()


@override_settings(
    BUILDER_MIN_CANDIDATE_SCORE=0.0,
    BUILDER_MAX_CANDIDATES=12,
    BUILDER_MAX_INPUTS=4,
)
class BuildPlanningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.release = BuilderModelRelease.objects.create(
            kind=BuilderModelKind.FOUNDATION,
            version="fake-v1",
            training_data_checksum="a" * 64,
            artifact_checksum="b" * 64,
            artifact=b"fake",
            is_active=True,
        )
        self.dataset_ids = []
        for index in range(2):
            dataset = Dataset.objects.create(
                title=f"Lung dataset {index}",
                description="lung clinical cohort",
            )
            version = DatasetVersion.objects.create(
                dataset=dataset,
                number=1,
                status=DatasetVersionStatus.AVAILABLE,
                checksum_sha256=str(index) * 64,
            )
            DatasetMembership.objects.create(user=self.user, dataset=dataset)
            analysis = DatasetAnalysis.objects.create(
                dataset_version=version,
                model_release=self.release,
                status=AnalysisStatus.COMPLETE,
                schema_fingerprint=f"schema-{index}",
            )
            table = DatasetTableSchema.objects.create(
                analysis=analysis,
                relative_path="patients.csv",
                format="csv",
                logical_name="patients",
                row_count=10,
                column_count=2,
                file_size_bytes=100,
                schema_fingerprint=f"table-{index}",
            )
            DatasetFieldSchema.objects.create(
                table=table,
                ordinal=0,
                name="patient_id",
                physical_type="VARCHAR",
                semantic_type=SemanticFieldType.PATIENT_ID,
                semantic_confidence=Decimal("0.99"),
                privacy_class=PrivacyClass.PSEUDONYMOUS_IDENTIFIER,
                join_candidate=True,
                non_null_count=10,
                distinct_count=10,
                unique_ratio=Decimal("1"),
            )
            DatasetFieldSchema.objects.create(
                table=table,
                ordinal=1,
                name=f"value_{index}",
                physical_type="VARCHAR",
                semantic_type=SemanticFieldType.CATEGORICAL,
                semantic_confidence=Decimal("0.80"),
                privacy_class=PrivacyClass.NON_SENSITIVE,
                non_null_count=10,
            )
            self.dataset_ids.append(dataset.pk)

    def test_plan_uses_only_authorized_exact_pseudonymous_inputs(self):
        request = BuildRequest.objects.create(
            user=self.user,
            prompt="Combine lung datasets",
            purpose="Authorized internal research",
            privacy_acknowledged=True,
            requested_dataset_ids=self.dataset_ids,
        )
        plan = BuildPlanningService(
            model_service=FakeModelService(self.release)
        ).plan(request.pk)
        self.assertEqual(plan.plan["join"]["semantic_type"], "patient_id")
        self.assertEqual(len(plan.plan["inputs"]), 2)
        self.assertEqual(plan.privacy_assessment.status, "approved")
        self.assertTrue(all(item["selected"] for item in request.candidates.values()))

    def test_explicit_dataset_without_membership_is_rejected(self):
        request = BuildRequest.objects.create(
            user=self.other,
            prompt="Combine lung datasets",
            purpose="Unauthorized test request",
            privacy_acknowledged=True,
            requested_dataset_ids=self.dataset_ids,
        )
        with self.assertRaises(BuildAuthorizationError):
            BuildPlanningService(
                model_service=FakeModelService(self.release)
            ).plan(request.pk)

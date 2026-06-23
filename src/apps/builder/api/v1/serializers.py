from django.conf import settings
from rest_framework import serializers

from apps.datasets.models import DatasetMembership, DatasetVersionStatus

from ...models import (
    BuildCandidate,
    BuildRequest,
    BuildRun,
    DatasetAnalysis,
    DatasetFieldSchema,
    DatasetTableSchema,
    DatasetTagPrediction,
    PrivacyAssessment,
    TransformationPlan,
)


class BuilderDatasetSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    origin = serializers.CharField()
    visibility = serializers.CharField()


class DatasetFieldSchemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetFieldSchema
        fields = (
            "id",
            "ordinal",
            "name",
            "physical_type",
            "nullable",
            "semantic_type",
            "semantic_confidence",
            "privacy_class",
            "join_candidate",
            "non_null_count",
            "distinct_count",
            "null_fraction",
            "unique_ratio",
            "value_profile",
        )


class DatasetTableSchemaSerializer(serializers.ModelSerializer):
    fields = DatasetFieldSchemaSerializer(many=True, read_only=True)

    class Meta:
        model = DatasetTableSchema
        fields = (
            "id",
            "relative_path",
            "format",
            "logical_name",
            "row_count",
            "column_count",
            "file_size_bytes",
            "schema_fingerprint",
            "fields",
        )


class DatasetTagPredictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetTagPrediction
        fields = ("namespace", "value", "confidence", "applied", "evidence")


class DatasetAnalysisSerializer(serializers.ModelSerializer):
    dataset = serializers.SerializerMethodField()
    dataset_version_id = serializers.UUIDField(source="dataset_version.id")
    version_number = serializers.IntegerField(source="dataset_version.number")
    model_version = serializers.CharField(
        source="model_release.version",
        allow_null=True,
    )
    tables = DatasetTableSchemaSerializer(many=True, read_only=True)
    tag_predictions = DatasetTagPredictionSerializer(many=True, read_only=True)
    is_terminal = serializers.BooleanField(read_only=True)

    class Meta:
        model = DatasetAnalysis
        fields = (
            "id",
            "dataset",
            "dataset_version_id",
            "version_number",
            "model_version",
            "status",
            "is_terminal",
            "progress",
            "progress_message",
            "schema_fingerprint",
            "summary",
            "error_code",
            "error_message",
            "queued_at",
            "started_at",
            "finished_at",
            "updated_at",
            "tables",
            "tag_predictions",
        )

    def get_fields(self):
        fields = super().get_fields()
        if not self.context.get("include_schema", True):
            fields.pop("tables", None)
        return fields

    def get_dataset(self, analysis):
        return BuilderDatasetSummarySerializer(
            analysis.dataset_version.dataset
        ).data


class BuildCandidateSerializer(serializers.ModelSerializer):
    dataset = serializers.SerializerMethodField()
    dataset_version_id = serializers.UUIDField(source="dataset_version.id")

    class Meta:
        model = BuildCandidate
        fields = (
            "id",
            "dataset",
            "dataset_version_id",
            "score",
            "selected",
            "matched_labels",
            "explanation",
        )

    def get_dataset(self, candidate):
        return BuilderDatasetSummarySerializer(candidate.dataset_version.dataset).data


class PrivacyAssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrivacyAssessment
        fields = (
            "status",
            "risk_level",
            "rules_version",
            "findings",
            "excluded_fields",
            "join_policy",
            "assessed_at",
        )


class TransformationPlanSerializer(serializers.ModelSerializer):
    model_version = serializers.CharField(source="model_release.version")
    privacy_assessment = PrivacyAssessmentSerializer(read_only=True)

    class Meta:
        model = TransformationPlan
        fields = (
            "id",
            "version",
            "status",
            "model_version",
            "plan",
            "plan_checksum",
            "privacy_assessment",
            "created_at",
        )


class BuildRunSerializer(serializers.ModelSerializer):
    output_dataset = BuilderDatasetSummarySerializer(read_only=True)
    output_version_id = serializers.UUIDField(
        source="output_version.id",
        allow_null=True,
    )
    is_terminal = serializers.BooleanField(read_only=True)
    poll_after_ms = serializers.SerializerMethodField()

    class Meta:
        model = BuildRun
        fields = (
            "id",
            "status",
            "is_terminal",
            "progress",
            "progress_message",
            "attempt_count",
            "output_dataset",
            "output_version_id",
            "output_row_count",
            "output_checksum_sha256",
            "error_code",
            "error_message",
            "queued_at",
            "started_at",
            "last_attempt_at",
            "finished_at",
            "created_at",
            "updated_at",
            "poll_after_ms",
        )

    @staticmethod
    def get_poll_after_ms(run):
        return None if run.is_terminal else settings.BUILDER_POLL_INTERVAL_MS


class BuildRequestCreateSerializer(serializers.Serializer):
    prompt = serializers.CharField(min_length=5, max_length=2000)
    purpose = serializers.CharField(min_length=10, max_length=1000)
    privacy_acknowledged = serializers.BooleanField()
    dataset_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        max_length=8,
    )

    def validate_privacy_acknowledged(self, value):
        if not value:
            raise serializers.ValidationError(
                "Privacy acknowledgement is required before automated linkage."
            )
        return value

    def validate_dataset_ids(self, values):
        unique = list(dict.fromkeys(values))
        if len(unique) != len(values):
            raise serializers.ValidationError("Dataset IDs must be unique.")
        user = self.context["request"].user
        accessible = set(
            DatasetMembership.objects.filter(
                user=user,
                dataset_id__in=unique,
                dataset__versions__status=DatasetVersionStatus.AVAILABLE,
            ).values_list("dataset_id", flat=True)
        )
        missing = set(unique) - accessible
        if missing:
            raise serializers.ValidationError(
                "One or more selected datasets are not in your library."
            )
        return unique


class BuildRequestSerializer(serializers.ModelSerializer):
    candidates = BuildCandidateSerializer(many=True, read_only=True)
    active_plan = serializers.SerializerMethodField()
    latest_run = serializers.SerializerMethodField()
    is_terminal = serializers.BooleanField(read_only=True)
    poll_after_ms = serializers.SerializerMethodField()

    class Meta:
        model = BuildRequest
        fields = (
            "id",
            "prompt",
            "purpose",
            "privacy_acknowledged",
            "requested_dataset_ids",
            "parsed_requirements",
            "status",
            "is_terminal",
            "progress",
            "progress_message",
            "error_code",
            "error_message",
            "queued_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "poll_after_ms",
            "candidates",
            "active_plan",
            "latest_run",
        )

    @staticmethod
    def get_active_plan(request):
        plan = next(
            (plan for plan in request.plans.all() if plan.status == "active"),
            None,
        )
        return TransformationPlanSerializer(plan).data if plan is not None else None

    @staticmethod
    def get_latest_run(request):
        run = next(iter(request.runs.all()), None)
        return BuildRunSerializer(run).data if run is not None else None

    @staticmethod
    def get_poll_after_ms(request):
        return None if request.is_terminal else settings.BUILDER_POLL_INTERVAL_MS

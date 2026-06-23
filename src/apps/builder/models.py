import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class BuilderModelKind(models.TextChoices):
    FOUNDATION = "foundation", "Foundation bundle"


class BuilderModelRelease(models.Model):
    """Versioned, checksum-verified model bundle used by the builder."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(
        max_length=32,
        choices=BuilderModelKind.choices,
        default=BuilderModelKind.FOUNDATION,
    )
    version = models.CharField(max_length=120, unique=True)
    training_data_checksum = models.CharField(max_length=64)
    artifact_checksum = models.CharField(max_length=64)
    artifact = models.BinaryField()
    metrics = models.JSONField(default=dict, blank=True)
    label_catalog = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("kind",),
                condition=Q(is_active=True),
                name="builder_model_active_kind_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.version


class AnalysisStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETE = "complete", "Complete"
    FAILED = "failed", "Failed"
    UNSUPPORTED = "unsupported", "Unsupported"


class DatasetAnalysis(models.Model):
    """Schema and semantic analysis for one immutable dataset version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.OneToOneField(
        "datasets.DatasetVersion",
        on_delete=models.CASCADE,
        related_name="builder_analysis",
    )
    model_release = models.ForeignKey(
        BuilderModelRelease,
        on_delete=models.PROTECT,
        related_name="dataset_analyses",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=AnalysisStatus.choices,
        default=AnalysisStatus.PENDING,
    )
    celery_task_id = models.CharField(max_length=255, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True)
    schema_fingerprint = models.CharField(max_length=64, blank=True)
    dataset_text_checksum = models.CharField(max_length=64, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("status", "-created_at"),
                name="builder_analysis_status_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            AnalysisStatus.COMPLETE,
            AnalysisStatus.FAILED,
            AnalysisStatus.UNSUPPORTED,
        }

    def __str__(self) -> str:
        return f"Analysis of {self.dataset_version}"


class DatasetTableSchema(models.Model):
    analysis = models.ForeignKey(
        DatasetAnalysis,
        on_delete=models.CASCADE,
        related_name="tables",
    )
    relative_path = models.CharField(max_length=1000)
    format = models.CharField(max_length=24)
    logical_name = models.CharField(max_length=255)
    row_count = models.PositiveBigIntegerField(null=True, blank=True)
    column_count = models.PositiveIntegerField(default=0)
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    schema_fingerprint = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("relative_path",)
        constraints = [
            models.UniqueConstraint(
                fields=("analysis", "relative_path"),
                name="builder_analysis_table_path_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.analysis.dataset_version}: {self.relative_path}"


class SemanticFieldType(models.TextChoices):
    PATIENT_ID = "patient_id", "Patient identifier"
    SUBJECT_ID = "subject_id", "Subject identifier"
    STUDY_ID = "study_id", "Study identifier"
    ENCOUNTER_ID = "encounter_id", "Encounter identifier"
    IMAGE_ID = "image_id", "Image identifier"
    GENERIC_ID = "generic_id", "Generic identifier"
    AGE = "age", "Age"
    SEX = "sex", "Sex"
    CITY = "city", "City"
    LOCATION = "location", "Location"
    SMOKING_STATUS = "smoking_status", "Smoking status"
    DIAGNOSIS = "diagnosis", "Diagnosis"
    CANCER_STATUS = "cancer_status", "Cancer status"
    TIMESTAMP = "timestamp", "Timestamp"
    MODALITY = "modality", "Modality"
    TARGET = "target", "Target"
    FREE_TEXT = "free_text", "Free text"
    NUMERIC_MEASUREMENT = "numeric_measurement", "Numeric measurement"
    CATEGORICAL = "categorical", "Categorical value"
    UNKNOWN = "unknown", "Unknown"


class PrivacyClass(models.TextChoices):
    NON_SENSITIVE = "non_sensitive", "Non-sensitive"
    SENSITIVE = "sensitive", "Sensitive"
    QUASI_IDENTIFIER = "quasi_identifier", "Quasi-identifier"
    DIRECT_IDENTIFIER = "direct_identifier", "Direct identifier"
    PSEUDONYMOUS_IDENTIFIER = (
        "pseudonymous_identifier",
        "Pseudonymous identifier",
    )


class DatasetFieldSchema(models.Model):
    table = models.ForeignKey(
        DatasetTableSchema,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    ordinal = models.PositiveIntegerField()
    name = models.CharField(max_length=500)
    physical_type = models.CharField(max_length=255)
    nullable = models.BooleanField(default=True)
    semantic_type = models.CharField(
        max_length=40,
        choices=SemanticFieldType.choices,
        default=SemanticFieldType.UNKNOWN,
    )
    semantic_confidence = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        default=0,
    )
    privacy_class = models.CharField(
        max_length=40,
        choices=PrivacyClass.choices,
        default=PrivacyClass.NON_SENSITIVE,
    )
    join_candidate = models.BooleanField(default=False)
    non_null_count = models.PositiveBigIntegerField(default=0)
    distinct_count = models.PositiveBigIntegerField(null=True, blank=True)
    null_fraction = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    unique_ratio = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    value_profile = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("ordinal",)
        constraints = [
            models.UniqueConstraint(
                fields=("table", "ordinal"),
                name="builder_table_field_ordinal_uniq",
            ),
            models.UniqueConstraint(
                fields=("table", "name"),
                name="builder_table_field_name_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("semantic_type", "join_candidate"),
                name="builder_field_sem_join_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.table.logical_name}.{self.name}"


class PredictionNamespace(models.TextChoices):
    ANATOMICAL_AREA = "anatomical_area", "Anatomical area"
    MODALITY = "modality", "Modality"
    ML_TASK = "ml_task", "ML task"
    TAG = "tag", "Tag"


class DatasetTagPrediction(models.Model):
    analysis = models.ForeignKey(
        DatasetAnalysis,
        on_delete=models.CASCADE,
        related_name="tag_predictions",
    )
    namespace = models.CharField(
        max_length=32,
        choices=PredictionNamespace.choices,
    )
    value = models.CharField(max_length=100)
    confidence = models.DecimalField(max_digits=6, decimal_places=5)
    applied = models.BooleanField(default=False)
    evidence = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("namespace", "-confidence", "value")
        constraints = [
            models.UniqueConstraint(
                fields=("analysis", "namespace", "value"),
                name="builder_analysis_tag_pred_uniq",
            ),
        ]


class BuildRequestStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    PLANNING = "planning", "Planning"
    READY = "ready", "Ready"
    BUILDING = "building", "Building"
    SUCCEEDED = "succeeded", "Succeeded"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"


class BuildRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dataset_build_requests",
    )
    prompt = models.TextField()
    purpose = models.TextField()
    privacy_acknowledged = models.BooleanField(default=False)
    requested_dataset_ids = models.JSONField(default=list, blank=True)
    parsed_requirements = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=BuildRequestStatus.choices,
        default=BuildRequestStatus.QUEUED,
    )
    celery_task_id = models.CharField(max_length=255, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("user", "-created_at"),
                name="builder_req_user_idx",
            ),
            models.Index(
                fields=("status", "-created_at"),
                name="builder_req_status_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            BuildRequestStatus.SUCCEEDED,
            BuildRequestStatus.REJECTED,
            BuildRequestStatus.FAILED,
        }

    def __str__(self) -> str:
        return f"{self.user}: {self.prompt[:60]}"


class BuildCandidate(models.Model):
    request = models.ForeignKey(
        BuildRequest,
        on_delete=models.CASCADE,
        related_name="candidates",
    )
    dataset_version = models.ForeignKey(
        "datasets.DatasetVersion",
        on_delete=models.PROTECT,
        related_name="builder_candidates",
    )
    score = models.DecimalField(max_digits=8, decimal_places=6)
    selected = models.BooleanField(default=False)
    matched_labels = models.JSONField(default=list, blank=True)
    explanation = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-score", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("request", "dataset_version"),
                name="builder_request_candidate_uniq",
            ),
        ]


class TransformationPlanStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUPERSEDED = "superseded", "Superseded"
    INVALID = "invalid", "Invalid"


class TransformationPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        BuildRequest,
        on_delete=models.CASCADE,
        related_name="plans",
    )
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=TransformationPlanStatus.choices,
        default=TransformationPlanStatus.ACTIVE,
    )
    model_release = models.ForeignKey(
        BuilderModelRelease,
        on_delete=models.PROTECT,
        related_name="transformation_plans",
    )
    plan = models.JSONField()
    plan_checksum = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dataset_transformation_plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(
                fields=("request", "version"),
                name="builder_request_plan_version_uniq",
            ),
            models.UniqueConstraint(
                fields=("request",),
                condition=Q(status=TransformationPlanStatus.ACTIVE),
                name="builder_request_active_plan_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Plan {self.request_id} v{self.version}"


class PrivacyAssessmentStatus(models.TextChoices):
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    REVIEW_REQUIRED = "review_required", "Review required"


class PrivacyAssessment(models.Model):
    plan = models.OneToOneField(
        TransformationPlan,
        on_delete=models.CASCADE,
        related_name="privacy_assessment",
    )
    status = models.CharField(
        max_length=24,
        choices=PrivacyAssessmentStatus.choices,
    )
    risk_level = models.CharField(max_length=16)
    rules_version = models.CharField(max_length=64)
    findings = models.JSONField(default=list, blank=True)
    excluded_fields = models.JSONField(default=list, blank=True)
    join_policy = models.JSONField(default=dict, blank=True)
    assessed_at = models.DateTimeField(auto_now_add=True)


class BuildRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    RETRYING = "retrying", "Retrying"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REJECTED = "rejected", "Rejected"


class BuildRun(models.Model):
    ACTIVE_STATUSES = (
        BuildRunStatus.QUEUED,
        BuildRunStatus.RUNNING,
        BuildRunStatus.RETRYING,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        BuildRequest,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    plan = models.ForeignKey(
        TransformationPlan,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dataset_build_runs",
    )
    output_dataset = models.ForeignKey(
        "datasets.Dataset",
        on_delete=models.SET_NULL,
        related_name="builder_output_runs",
        null=True,
        blank=True,
    )
    output_version = models.ForeignKey(
        "datasets.DatasetVersion",
        on_delete=models.SET_NULL,
        related_name="builder_output_runs",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=BuildRunStatus.choices,
        default=BuildRunStatus.QUEUED,
    )
    celery_task_id = models.CharField(max_length=255, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True)
    output_row_count = models.PositiveBigIntegerField(null=True, blank=True)
    output_checksum_sha256 = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("request",),
                condition=Q(status__in=("queued", "running", "retrying")),
                name="builder_request_active_run_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("requested_by", "-created_at"),
                name="builder_run_user_idx",
            ),
            models.Index(
                fields=("status", "-created_at"),
                name="builder_run_status_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            BuildRunStatus.SUCCEEDED,
            BuildRunStatus.FAILED,
            BuildRunStatus.REJECTED,
        }


class BuildInput(models.Model):
    run = models.ForeignKey(
        BuildRun,
        on_delete=models.CASCADE,
        related_name="inputs",
    )
    dataset_version = models.ForeignKey(
        "datasets.DatasetVersion",
        on_delete=models.PROTECT,
        related_name="builder_inputs",
    )
    table_schema = models.ForeignKey(
        DatasetTableSchema,
        on_delete=models.PROTECT,
        related_name="builder_inputs",
    )
    join_field = models.ForeignKey(
        DatasetFieldSchema,
        on_delete=models.PROTECT,
        related_name="builder_join_inputs",
    )
    position = models.PositiveIntegerField()
    schema_fingerprint = models.CharField(max_length=64)

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("run", "dataset_version"),
                name="builder_run_input_version_uniq",
            ),
            models.UniqueConstraint(
                fields=("run", "position"),
                name="builder_run_input_position_uniq",
            ),
        ]


class DatasetLineage(models.Model):
    output_version = models.ForeignKey(
        "datasets.DatasetVersion",
        on_delete=models.CASCADE,
        related_name="lineage_inputs",
    )
    input_version = models.ForeignKey(
        "datasets.DatasetVersion",
        on_delete=models.PROTECT,
        related_name="lineage_outputs",
    )
    build_run = models.ForeignKey(
        BuildRun,
        on_delete=models.PROTECT,
        related_name="lineage",
    )
    transformation_plan = models.ForeignKey(
        TransformationPlan,
        on_delete=models.PROTECT,
        related_name="lineage",
    )
    role = models.CharField(max_length=32, default="join_input")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("output_version", "input_version"),
                name="builder_lineage_output_input_uniq",
            ),
        ]

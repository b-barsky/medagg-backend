import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AnatomicalArea(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self) -> str:
        return self.name


class Modality(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self) -> str:
        return self.name


class MLTask(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self) -> str:
        return self.name


class DatasetOrigin(models.TextChoices):
    IMPORTED = "imported", "Imported"
    DERIVED = "derived", "Derived"
    MANUAL = "manual", "Manual"


class DatasetVisibility(models.TextChoices):
    PUBLIC = "public", "Public"
    INTERNAL = "internal", "Internal"
    PRIVATE = "private", "Private"


class Dataset(models.Model):
    """
    A logical dataset managed by Medagg.

    Remote catalog entries live in catalog.SourceDataset. A Dataset appears
    here only after it is imported, created manually, or produced by a future
    builder run.
    """

    source_dataset = models.OneToOneField(
        "catalog.SourceDataset",
        on_delete=models.PROTECT,
        related_name="local_dataset",
        null=True,
        blank=True,
    )
    origin = models.CharField(
        max_length=16,
        choices=DatasetOrigin.choices,
        default=DatasetOrigin.MANUAL,
    )
    visibility = models.CharField(
        max_length=16,
        choices=DatasetVisibility.choices,
        default=DatasetVisibility.INTERNAL,
    )

    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    license = models.CharField(max_length=500, blank=True)
    license_names = models.JSONField(default=list, blank=True)
    legacy_metadata = models.JSONField(default=dict, blank=True)

    record_count = models.PositiveBigIntegerField(
        blank=True,
        null=True,
    )
    size_bytes = models.PositiveBigIntegerField(
        blank=True,
        null=True,
    )

    anatomical_area = models.ForeignKey(
        AnatomicalArea,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    modalities = models.ManyToManyField(
        Modality,
        through="DatasetModality",
    )
    ml_tasks = models.ManyToManyField(
        MLTask,
        through="DatasetMLTask",
    )
    tags = models.ManyToManyField(
        Tag,
        through="DatasetTag",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return self.title


class DatasetVersionStatus(models.TextChoices):
    STAGING = "staging", "Staging"
    AVAILABLE = "available", "Available"
    FAILED = "failed", "Failed"


class DatasetVersion(models.Model):
    """An immutable materialized version of a managed Dataset."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=DatasetVersionStatus.choices,
        default=DatasetVersionStatus.STAGING,
    )

    source_revision = models.CharField(max_length=64, blank=True)
    source_version = models.CharField(max_length=100, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)

    checksum_sha256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    record_count = models.PositiveBigIntegerField(null=True, blank=True)
    manifest = models.JSONField(default=dict, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    available_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-number",)
        constraints = [
            models.UniqueConstraint(
                fields=("dataset", "number"),
                name="datasets_version_number_uniq",
            ),
            models.UniqueConstraint(
                fields=("dataset", "source_revision"),
                condition=~Q(source_revision=""),
                name="datasets_version_revision_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("dataset", "status", "-number"),
                name="datasets_ver_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dataset.title} v{self.number}"


class DatasetArtifactKind(models.TextChoices):
    SOURCE_ARCHIVE = "source_archive", "Source archive"
    DATA = "data", "Data"
    MANIFEST = "manifest", "Manifest"


class DatasetArtifact(models.Model):
    """One immutable object stored in an S3-compatible object store."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    kind = models.CharField(
        max_length=32,
        choices=DatasetArtifactKind.choices,
        default=DatasetArtifactKind.SOURCE_ARCHIVE,
    )
    storage_backend = models.CharField(
        max_length=32,
        default="s3",
    )
    bucket = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024)
    object_version_id = models.CharField(max_length=255, blank=True)

    filename = models.CharField(max_length=500)
    content_type = models.CharField(
        max_length=255,
        default="application/octet-stream",
    )
    size_bytes = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    etag = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("kind", "filename")
        constraints = [
            models.UniqueConstraint(
                fields=("dataset_version", "kind", "object_key"),
                name="datasets_artifact_object_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("checksum_sha256",),
                name="datasets_artifact_sha_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.filename


class DatasetImportStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    RETRYING = "retrying", "Retrying"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REJECTED = "rejected", "Rejected"


class DatasetImport(models.Model):
    """Durable audit and execution state for one explicit import request."""

    ACTIVE_STATUSES = (
        DatasetImportStatus.QUEUED,
        DatasetImportStatus.RUNNING,
        DatasetImportStatus.RETRYING,
    )
    TERMINAL_STATUSES = (
        DatasetImportStatus.SUCCEEDED,
        DatasetImportStatus.FAILED,
        DatasetImportStatus.REJECTED,
    )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    source_dataset = models.ForeignKey(
        "catalog.SourceDataset",
        on_delete=models.PROTECT,
        related_name="import_runs",
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        related_name="import_runs",
        null=True,
        blank=True,
    )
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="import_runs",
        null=True,
        blank=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_imports",
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=16,
        choices=DatasetImportStatus.choices,
        default=DatasetImportStatus.QUEUED,
    )
    celery_task_id = models.CharField(max_length=255, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)

    source_revision = models.CharField(max_length=64)
    source_version = models.CharField(max_length=100, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)

    accepted_license = models.BooleanField(default=False)
    license_names_snapshot = models.JSONField(default=list, blank=True)
    license_fingerprint = models.CharField(max_length=64)
    policy_snapshot = models.JSONField(default=dict, blank=True)

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
                fields=("source_dataset", "source_revision"),
                condition=Q(status__in=("queued", "running", "retrying")),
                name="datasets_active_import_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "-created_at"),
                name="datasets_import_status_idx",
            ),
            models.Index(
                fields=("source_dataset", "source_revision"),
                name="datasets_import_source_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def __str__(self) -> str:
        return f"{self.source_dataset} ({self.status})"


class DatasetMembershipAcquisition(models.TextChoices):
    IMPORTED = "imported", "Imported"
    SHARED = "shared", "Shared"
    DERIVED = "derived", "Derived"
    MANUAL = "manual", "Manual"


class DatasetMembership(models.Model):
    """
    Grants one user access to one managed dataset.

    The dataset, versions, and artifacts remain shared. Membership records are
    the user's personal library and never duplicate the stored object.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dataset_memberships",
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    first_import = models.ForeignKey(
        DatasetImport,
        on_delete=models.SET_NULL,
        related_name="granted_memberships",
        null=True,
        blank=True,
    )
    acquisition = models.CharField(
        max_length=16,
        choices=DatasetMembershipAcquisition.choices,
        default=DatasetMembershipAcquisition.IMPORTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "dataset"),
                name="datasets_member_user_ds_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "-created_at"),
                name="datasets_member_user_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> {self.dataset}"


class DatasetImportRequester(models.Model):
    """A user who requested or joined a durable shared import run."""

    import_run = models.ForeignKey(
        DatasetImport,
        on_delete=models.CASCADE,
        related_name="requesters",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dataset_import_requests",
    )
    accepted_license = models.BooleanField(default=True)
    license_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    access_granted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("import_run", "user"),
                name="datasets_req_import_user_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "-created_at"),
                name="datasets_req_user_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} requested {self.import_run_id}"


class DatasetModality(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    modality = models.ForeignKey(Modality, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("dataset", "modality"),
                name="datasets_dataset_modality_uniq",
            ),
        ]


class DatasetMLTask(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    ml_task = models.ForeignKey(MLTask, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("dataset", "ml_task"),
                name="datasets_dataset_ml_task_uniq",
            ),
        ]


class DatasetTag(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("dataset", "tag"),
                name="datasets_dataset_tag_uniq",
            ),
        ]

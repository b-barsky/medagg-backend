from django.db import models
from django.utils import timezone


class MetadataStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETE = "complete", "Complete"
    STALE = "stale", "Stale"
    FAILED = "failed", "Failed"


class DataSource(models.Model):
    """
    An external catalog from which dataset metadata is collected.

    The slug must match a provider registered in ProviderRegistry.
    """

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    base_url = models.URLField(max_length=500)
    is_enabled = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        self.slug = self.slug.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class SourceDataset(models.Model):
    """
    Provider-owned metadata for one externally hosted dataset.

    This model does not imply that the dataset files have been
    downloaded or imported into the platform.
    """

    source = models.ForeignKey(DataSource, on_delete=models.PROTECT, related_name="datasets")

    external_id = models.CharField(max_length=500)
    source_url = models.URLField(max_length=1000)

    title = models.CharField(max_length=500)
    subtitle = models.TextField(blank=True)
    description = models.TextField(blank=True)

    owner_name = models.CharField(max_length=255, blank=True)
    owner_ref = models.CharField(max_length=255, blank=True)
    license_name = models.CharField(max_length=255, blank=True)
    license_names = models.JSONField(default=list, blank=True)

    total_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    download_count = models.PositiveBigIntegerField(null=True, blank=True)
    vote_count = models.PositiveBigIntegerField(null=True, blank=True)
    view_count = models.PositiveBigIntegerField(null=True, blank=True)

    usability_rating = models.DecimalField(max_digits=6, decimal_places=5, null=True, blank=True)

    remote_version = models.CharField(max_length=100, blank=True)
    remote_updated_at = models.DateTimeField(null=True, blank=True)

    is_private = models.BooleanField(default=False)

    thumbnail_url = models.URLField(max_length=1000, blank=True)

    summary_metadata = models.JSONField(default=dict, blank=True)
    detail_metadata = models.JSONField(default=dict, blank=True)
    detail_status = models.CharField(max_length=16, choices=MetadataStatus.choices, default=MetadataStatus.PENDING)
    detail_fetched_at = models.DateTimeField(null=True, blank=True)
    detail_source_updated_at = models.DateTimeField(null=True, blank=True)
    detail_error = models.TextField(blank=True)

    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-remote_updated_at", "-id",)
        constraints = [models.UniqueConstraint(fields=("source", "external_id"), name="catalog_src_ext_id_uniq")]
        indexes = [models.Index(fields=("source", "-remote_updated_at"), name="catalog_src_remote_idx"),
                   models.Index(fields=("source", "last_seen_at"), name="catalog_src_seen_idx")]

    def __str__(self) -> str:
        return f"{self.source.slug}:{self.external_id}"

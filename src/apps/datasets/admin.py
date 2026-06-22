from django.contrib import admin

from .models import (
    AnatomicalArea,
    Dataset,
    DatasetArtifact,
    DatasetImport,
    DatasetImportRequester,
    DatasetMembership,
    DatasetMLTask,
    DatasetModality,
    DatasetTag,
    DatasetVersion,
    MLTask,
    Modality,
    Tag,
)


@admin.register(AnatomicalArea, Modality, MLTask, Tag)
class NamedTaxonomyAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    ordering = ("name",)


class DatasetArtifactInline(admin.TabularInline):
    model = DatasetArtifact
    extra = 0
    can_delete = False
    readonly_fields = (
        "id",
        "kind",
        "bucket",
        "object_key",
        "object_version_id",
        "filename",
        "content_type",
        "size_bytes",
        "checksum_sha256",
        "etag",
        "created_at",
    )


@admin.register(DatasetVersion)
class DatasetVersionAdmin(admin.ModelAdmin):
    list_display = (
        "dataset",
        "number",
        "status",
        "source_version",
        "size_bytes",
        "available_at",
    )
    list_filter = ("status",)
    search_fields = (
        "dataset__title",
        "source_revision",
        "checksum_sha256",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "available_at",
    )
    inlines = (DatasetArtifactInline,)


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "origin",
        "visibility",
        "size_bytes",
        "updated_at",
    )
    list_filter = ("origin", "visibility")
    search_fields = (
        "title",
        "source_dataset__external_id",
    )
    readonly_fields = (
        "legacy_metadata",
        "created_at",
        "updated_at",
    )


@admin.register(DatasetArtifact)
class DatasetArtifactAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "dataset_version",
        "kind",
        "size_bytes",
        "created_at",
    )
    search_fields = (
        "filename",
        "object_key",
        "checksum_sha256",
    )
    readonly_fields = (
        "id",
        "dataset_version",
        "kind",
        "storage_backend",
        "bucket",
        "object_key",
        "object_version_id",
        "filename",
        "content_type",
        "size_bytes",
        "checksum_sha256",
        "etag",
        "created_at",
    )


class DatasetImportRequesterInline(admin.TabularInline):
    model = DatasetImportRequester
    extra = 0
    can_delete = False
    readonly_fields = (
        "user",
        "accepted_license",
        "license_fingerprint",
        "created_at",
        "access_granted_at",
    )


@admin.register(DatasetImport)
class DatasetImportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_dataset",
        "status",
        "attempt_count",
        "requested_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "source_dataset__source")
    search_fields = (
        "id",
        "source_dataset__external_id",
        "source_revision",
        "celery_task_id",
        "requesters__user__username",
    )
    readonly_fields = (
        "id",
        "source_dataset",
        "dataset",
        "dataset_version",
        "requested_by",
        "status",
        "celery_task_id",
        "attempt_count",
        "source_revision",
        "source_version",
        "source_updated_at",
        "accepted_license",
        "license_names_snapshot",
        "license_fingerprint",
        "policy_snapshot",
        "error_code",
        "error_message",
        "queued_at",
        "started_at",
        "last_attempt_at",
        "finished_at",
        "created_at",
        "updated_at",
    )
    inlines = (DatasetImportRequesterInline,)


@admin.register(DatasetMembership)
class DatasetMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "dataset",
        "acquisition",
        "created_at",
    )
    list_filter = ("acquisition",)
    search_fields = (
        "user__username",
        "user__email",
        "dataset__title",
    )
    readonly_fields = (
        "user",
        "dataset",
        "first_import",
        "acquisition",
        "created_at",
        "updated_at",
    )


@admin.register(DatasetImportRequester)
class DatasetImportRequesterAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "import_run",
        "accepted_license",
        "created_at",
        "access_granted_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "import_run__id",
    )
    readonly_fields = (
        "import_run",
        "user",
        "accepted_license",
        "license_fingerprint",
        "created_at",
        "access_granted_at",
    )


admin.site.register(DatasetModality)
admin.site.register(DatasetMLTask)
admin.site.register(DatasetTag)

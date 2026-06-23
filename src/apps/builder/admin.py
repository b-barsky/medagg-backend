from django.contrib import admin

from .models import (
    BuilderModelRelease,
    BuildRequest,
    BuildRun,
    DatasetAnalysis,
    DatasetFieldSchema,
    DatasetLineage,
    DatasetTableSchema,
    PrivacyAssessment,
    TransformationPlan,
)


@admin.register(BuilderModelRelease)
class BuilderModelReleaseAdmin(admin.ModelAdmin):
    list_display = ("version", "kind", "is_active", "created_at", "activated_at")
    list_filter = ("kind", "is_active")
    search_fields = ("version", "training_data_checksum", "artifact_checksum")
    readonly_fields = (
        "created_at",
        "activated_at",
        "training_data_checksum",
        "artifact_checksum",
        "metrics",
        "label_catalog",
    )


@admin.register(DatasetAnalysis)
class DatasetAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "dataset_version",
        "status",
        "progress",
        "model_release",
        "updated_at",
    )
    list_filter = ("status", "model_release")
    search_fields = ("dataset_version__dataset__title", "schema_fingerprint")
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")


@admin.register(DatasetTableSchema)
class DatasetTableSchemaAdmin(admin.ModelAdmin):
    list_display = ("logical_name", "analysis", "format", "row_count", "column_count")
    list_filter = ("format",)
    search_fields = ("relative_path", "analysis__dataset_version__dataset__title")


@admin.register(DatasetFieldSchema)
class DatasetFieldSchemaAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "table",
        "semantic_type",
        "privacy_class",
        "join_candidate",
        "semantic_confidence",
    )
    list_filter = ("semantic_type", "privacy_class", "join_candidate")
    search_fields = ("name", "table__logical_name")


@admin.register(BuildRequest)
class BuildRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "progress", "created_at")
    list_filter = ("status", "privacy_acknowledged")
    search_fields = ("id", "user__username", "prompt", "purpose")
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")


@admin.register(TransformationPlan)
class TransformationPlanAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "version", "status", "created_at")
    list_filter = ("status", "model_release")
    readonly_fields = ("plan_checksum", "plan", "created_at")


@admin.register(PrivacyAssessment)
class PrivacyAssessmentAdmin(admin.ModelAdmin):
    list_display = ("plan", "status", "risk_level", "rules_version", "assessed_at")
    list_filter = ("status", "risk_level", "rules_version")
    readonly_fields = (
        "findings",
        "excluded_fields",
        "join_policy",
        "assessed_at",
    )


@admin.register(BuildRun)
class BuildRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "requested_by",
        "status",
        "progress",
        "output_dataset",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("id", "requested_by__username", "request__prompt")
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")


@admin.register(DatasetLineage)
class DatasetLineageAdmin(admin.ModelAdmin):
    list_display = ("output_version", "input_version", "build_run", "role")
    search_fields = (
        "output_version__dataset__title",
        "input_version__dataset__title",
    )

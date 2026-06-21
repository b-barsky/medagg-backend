from django.contrib import admin

from .models import DataSource, SourceDataset


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_filter = ("is_enabled",)  # DJango requires it to be a list or tuple
    list_display = ("name", "slug", "is_enabled", "updated_at")
    search_fields = ("name", "slug")


@admin.register(SourceDataset)
class SourceDatasetAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "external_id", "detail_status", "remote_updated_at", "detail_fetched_at",
                    "last_seen_at")
    list_filter = ("source", "detail_status", "is_private")
    search_fields = ("title", "external_id", "owner_name")
    readonly_fields = ("created_at", "updated_at", "last_seen_at", "detail_fetched_at", "detail_source_updated_at",
                       "detail_error")

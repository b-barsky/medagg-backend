from django.contrib import admin

from .models import (
    SearchProviderRun,
    SearchResult,
    SearchRun,
)


class ReadOnlyExecutionAdminMixin:
    """Prevent manual mutation of durable execution history."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ) -> bool:
        return False


class SearchProviderRunInline(admin.TabularInline):
    model = SearchProviderRun
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "source",
        "position",
        "status",
        "attempt_count",
        "result_count",
        "detail_completed_count",
        "detail_failed_count",
        "error_code",
        "started_at",
        "finished_at",
    )
    readonly_fields = fields

    def has_add_permission(
        self,
        request,
        obj=None,
    ) -> bool:
        return False


@admin.register(SearchRun)
class SearchRunAdmin(
    ReadOnlyExecutionAdminMixin,
    admin.ModelAdmin,
):
    list_display = (
        "id",
        "query",
        "status",
        "created_at",
        "finished_at",
    )
    list_filter = (
        "status",
        "created_at",
    )
    search_fields = (
        "=id",
        "query",
    )
    readonly_fields = (
        "id",
        "query",
        "status",
        "deadline_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    )
    inlines = (SearchProviderRunInline,)


@admin.register(SearchProviderRun)
class SearchProviderRunAdmin(
    ReadOnlyExecutionAdminMixin,
    admin.ModelAdmin,
):
    list_display = (
        "id",
        "search_run",
        "source",
        "status",
        "attempt_count",
        "result_count",
        "detail_completed_count",
        "detail_failed_count",
        "updated_at",
    )
    list_filter = (
        "status",
        "source",
    )
    search_fields = (
        "=search_run__id",
        "search_run__query",
        "source__slug",
        "=task_id",
    )
    readonly_fields = (
        "search_run",
        "source",
        "position",
        "provider_page",
        "status",
        "task_id",
        "attempt_count",
        "result_count",
        "detail_completed_count",
        "detail_failed_count",
        "error_code",
        "error_message",
        "started_at",
        "last_attempt_at",
        "finished_at",
        "created_at",
        "updated_at",
    )


@admin.register(SearchResult)
class SearchResultAdmin(
    ReadOnlyExecutionAdminMixin,
    admin.ModelAdmin,
):
    list_display = (
        "id",
        "search_run",
        "provider_run",
        "source_dataset",
        "rank",
        "enrichment_status",
        "enrichment_attempt_count",
        "enrichment_finished_at",
    )
    list_filter = (
        "enrichment_status",
        "provider_run__source",
        "created_at",
    )
    search_fields = (
        "=search_run__id",
        "source_dataset__title",
        "source_dataset__external_id",
        "=enrichment_task_id",
    )
    readonly_fields = (
        "search_run",
        "provider_run",
        "source_dataset",
        "rank",
        "enrichment_status",
        "enrichment_task_id",
        "enrichment_attempt_count",
        "enrichment_error_code",
        "enrichment_error_message",
        "enrichment_started_at",
        "enrichment_last_attempt_at",
        "enrichment_finished_at",
        "created_at",
    )

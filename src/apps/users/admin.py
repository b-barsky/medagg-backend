from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "organization",
        "job_title",
        "location",
        "updated_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "organization",
        "job_title",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )

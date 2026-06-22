from django.conf import settings
from rest_framework import permissions

from apps.datasets.models import DatasetVisibility


class DatasetImportPermission(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if not settings.DATASET_IMPORT_REQUIRE_AUTHENTICATION:
            return True

        return bool(
            request.user
            and request.user.is_authenticated
        )


class DatasetArtifactPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj) -> bool:
        dataset = obj.dataset_version.dataset

        if dataset.visibility == DatasetVisibility.PUBLIC:
            return True

        if (
            dataset.visibility == DatasetVisibility.INTERNAL
            and not settings.DATASET_IMPORT_REQUIRE_AUTHENTICATION
        ):
            return True

        if not request.user or not request.user.is_authenticated:
            return False

        if dataset.visibility == DatasetVisibility.PRIVATE:
            return bool(request.user.is_staff)

        return True

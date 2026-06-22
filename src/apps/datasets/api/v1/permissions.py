from rest_framework import permissions

from apps.datasets.models import DatasetMembership


class DatasetImportPermission(permissions.BasePermission):
    message = "Authentication is required to import datasets."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
        )


class DatasetArtifactPermission(permissions.BasePermission):
    message = "This artifact is not in your dataset library."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
        )

    def has_object_permission(self, request, view, obj) -> bool:
        return DatasetMembership.objects.filter(
            user=request.user,
            dataset_id=obj.dataset_version.dataset_id,
        ).exists()

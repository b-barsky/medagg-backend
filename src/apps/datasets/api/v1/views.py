from rest_framework import viewsets

from apps.datasets.services import DatasetService

from .serializers import DatasetDetailedSerializer


class DatasetsViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only dataset endpoint."""

    serializer_class = DatasetDetailedSerializer

    @property
    def _dataset_service(self) -> DatasetService:
        return DatasetService()

    def get_queryset(self):
        return self._dataset_service.get_all_detailed()
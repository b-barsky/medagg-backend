from rest_framework import viewsets
from rest_framework.response import Response

from apps.datasets.api.v1.serializers import (
    DatasetDetailedSerializer,
)
from apps.search.services import SearchService

from .serializers import (
    SearchDatasetsPostSerializer,
    SearchDatasetsRequestSerializer,
)


class BaseSearchViewSet(viewsets.GenericViewSet):
    """A ViewSet exposing POST create as a search operation."""

    def create(self, request, *args, **kwargs):
        return self.search(request=request)

    def search(self, request):
        raise NotImplementedError


class SearchDatasetsViewSet(BaseSearchViewSet):
    serializer_class = SearchDatasetsPostSerializer

    @property
    def _search_service(self) -> SearchService:
        return SearchService()

    def get_queryset(self):
        return self._search_service.default_datasets()

    def search(self, request):
        request_serializer = SearchDatasetsRequestSerializer(
            data={
                "get": request.query_params.dict(),
                "post": request.data,
            }
        )
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data

        result_set = self._search_service.search_datasets(
            query=validated_data["post"]["query"],
            filter_params=validated_data["get"],
        )

        page = self.paginate_queryset(result_set)
        objects = page if page is not None else result_set

        response_serializer = DatasetDetailedSerializer(
            objects,
            many=True,
            context=self.get_serializer_context(),
        )

        if page is not None:
            return self.get_paginated_response(
                response_serializer.data
            )

        return Response(response_serializer.data)
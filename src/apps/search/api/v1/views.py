import math

from django.conf import settings
from django.http import Http404
from rest_framework import (
    permissions,
    serializers,
    status,
    viewsets,
)
from rest_framework.response import Response

from apps.search.models import SearchRun
from apps.search.services import (
    SearchRunService,
    SearchSourceValidationError,
)

from .pagination import SearchResultPagination
from .serializers import (
    SearchRunCreateSerializer,
    SearchRunSerializer,
    SourceDatasetSearchResultSerializer,
)


class SearchDatasetsViewSet(viewsets.GenericViewSet):
    """
    Create and poll durable federated dataset searches.

    POST /search/datasets/ creates a SearchRun and returns HTTP 202.
    GET /search/datasets/{uuid}/ returns its latest state and results.
    """

    permission_classes = (permissions.AllowAny,)
    serializer_class = SearchRunCreateSerializer

    # GenericViewSet and DRF's browsable renderer expect a queryset even
    # though this endpoint retrieves runs through SearchRunService.
    queryset = SearchRun.objects.none()

    # Nested SearchResult records are paginated manually in _response_data().
    pagination_class = None
    filter_backends = ()

    http_method_names = (
        "get",
        "post",
        "head",
        "options",
    )

    lookup_value_regex = (
        "[0-9a-fA-F]{8}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{12}"
    )

    @property
    def search_service(self) -> SearchRunService:
        return SearchRunService()

    def create(self, request, *args, **kwargs):
        request_serializer = self.get_serializer(
            data=request.data,
        )
        request_serializer.is_valid(raise_exception=True)
        validated_data = request_serializer.validated_data

        try:
            search_run = self.search_service.create_run(
                query=validated_data["query"],
                source_slugs=validated_data.get("sources"),
                provider_page=validated_data["provider_page"],
            )
        except SearchSourceValidationError as exc:
            detail: object = str(exc)

            if exc.unavailable_sources:
                detail = {
                    "message": str(exc),
                    "unavailable": list(
                        exc.unavailable_sources
                    ),
                }

            raise serializers.ValidationError(
                {
                    "sources": detail,
                }
            ) from exc

        self.search_service.enqueue_run(search_run.pk)
        search_run = self.search_service.get_run(
            search_run.pk
        )
        response_data = self._response_data(
            request,
            search_run,
        )
        location = self.reverse_action(
            "detail",
            args=(search_run.pk,),
            request=request,
        )

        return Response(
            response_data,
            status=status.HTTP_202_ACCEPTED,
            headers={
                "Cache-Control": "no-store",
                "Location": location,
                "Retry-After": self._retry_after_seconds(),
            },
        )

    def retrieve(
        self,
        request,
        pk=None,
        *args,
        **kwargs,
    ):
        try:
            self.search_service.expire_run_if_needed(pk)
            search_run = self.search_service.get_run(pk)
        except SearchRun.DoesNotExist as exc:
            raise Http404(
                "Search run not found."
            ) from exc

        response = Response(
            self._response_data(
                request,
                search_run,
            )
        )
        response["Cache-Control"] = "no-store"

        if not search_run.is_terminal:
            response["Retry-After"] = (
                self._retry_after_seconds()
            )

        return response

    def _response_data(
        self,
        request,
        search_run: SearchRun,
    ) -> dict[str, object]:
        result_queryset = (
            self.search_service.results_queryset(
                search_run.pk
            )
        )
        paginator = SearchResultPagination()
        result_page = paginator.paginate_queryset(
            result_queryset,
            request,
            view=self,
        )

        if result_page is None:
            result_page = list(result_queryset)

        result_serializer = (
            SourceDatasetSearchResultSerializer(
                result_page,
                many=True,
                context=self.get_serializer_context(),
            )
        )
        response_data = dict(
            SearchRunSerializer(
                search_run,
                context=self.get_serializer_context(),
            ).data
        )
        response_data["poll_after_ms"] = (
            None
            if search_run.is_terminal
            else settings.SEARCH_POLL_INTERVAL_MS
        )
        response_data["results"] = paginator.payload(
            result_serializer.data
        )

        return response_data

    @staticmethod
    def _retry_after_seconds() -> str:
        return str(
            max(
                1,
                math.ceil(
                    settings.SEARCH_POLL_INTERVAL_MS
                    / 1000
                ),
            )
        )

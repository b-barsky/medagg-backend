import math

from django.conf import settings
from django.db.models import Prefetch, Q
from django.http import Http404
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.datasets.access import (
    DatasetAccessDenied,
    DatasetAccessService,
)
from apps.datasets.models import (
    DatasetArtifact,
    DatasetImport,
    DatasetImportStatus,
    DatasetMembership,
)
from apps.datasets.services import (
    DatasetImportError,
    DatasetImportPolicyRejected,
    DatasetImportService,
    DatasetService,
)
from apps.datasets.storage import ObjectStorageError, S3ObjectStorage

from .permissions import (
    DatasetArtifactPermission,
    DatasetImportPermission,
)
from .serializers import (
    DatasetArtifactSerializer,
    DatasetDetailedSerializer,
    DatasetImportCreateSerializer,
    DatasetImportSerializer,
)


class DatasetsViewSet(viewsets.ReadOnlyModelViewSet):
    """The authenticated user's personal dataset library."""

    serializer_class = DatasetDetailedSerializer
    permission_classes = (permissions.IsAuthenticated,)

    @property
    def dataset_service(self) -> DatasetService:
        return DatasetService()

    def get_queryset(self):
        user = self.request.user

        if not getattr(user, "is_authenticated", False):
            return self.dataset_service.get_all_detailed().none()

        memberships = DatasetMembership.objects.filter(
            user=user
        ).select_related("first_import")

        return (
            self.dataset_service.get_all_detailed()
            .filter(memberships__user=user)
            .prefetch_related(
                Prefetch(
                    "memberships",
                    queryset=memberships,
                    to_attr="current_user_memberships",
                )
            )
            .distinct()
        )


class DatasetImportViewSet(viewsets.GenericViewSet):
    queryset = DatasetImport.objects.none()
    permission_classes = (DatasetImportPermission,)
    serializer_class = DatasetImportSerializer
    pagination_class = None
    filter_backends = ()
    http_method_names = ("get", "post", "head", "options")
    lookup_value_regex = (
        "[0-9a-fA-F]{8}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{12}"
    )

    @property
    def import_service(self) -> DatasetImportService:
        return DatasetImportService()

    @property
    def access_service(self) -> DatasetAccessService:
        return DatasetAccessService(self.import_service)

    def get_queryset(self):
        user = self.request.user

        if not getattr(user, "is_authenticated", False):
            return DatasetImport.objects.none()

        return (
            DatasetImport.objects.select_related(
                "source_dataset__source",
                "dataset",
                "dataset_version",
            )
            .prefetch_related(
                "dataset_version__artifacts",
                "requesters",
            )
            .filter(
                Q(requesters__user=user)
                | Q(requested_by=user)
            )
            .distinct()
        )

    def get_serializer_class(self):
        if self.action == "create":
            return DatasetImportCreateSerializer

        return DatasetImportSerializer

    def create(self, request, *args, **kwargs):
        request_serializer = self.get_serializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        validated = request_serializer.validated_data

        try:
            access_result = self.access_service.request_import(
                user=request.user,
                source_dataset_id=validated["source_dataset"].pk,
                accepted_license=validated["accept_license"],
                license_fingerprint=validated["license_fingerprint"],
            )
        except DatasetAccessDenied as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DatasetImportPolicyRejected as exc:
            return Response(
                {
                    "detail": exc.decision.message,
                    "policy": exc.decision.as_dict(),
                },
                status=status.HTTP_409_CONFLICT,
            )

        creation = access_result.creation
        import_run = creation.import_run

        if creation.created:
            try:
                self.import_service.enqueue_import(import_run.pk)
            except DatasetImportError as exc:
                return Response(
                    {"detail": str(exc)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        # A reused version or a concurrently completed worker may already be
        # available. This grant only creates a membership and never copies an
        # object-storage artifact.
        self.access_service.grant_import_access(import_run.pk)
        import_run = self.import_service.get_import(import_run.pk)

        response_status = (
            status.HTTP_200_OK
            if (
                creation.reused_version
                or import_run.status == DatasetImportStatus.SUCCEEDED
            )
            else status.HTTP_202_ACCEPTED
        )
        location = self.reverse_action(
            "detail",
            args=(import_run.pk,),
            request=request,
        )
        response = Response(
            DatasetImportSerializer(
                import_run,
                context=self.get_serializer_context(),
            ).data,
            status=response_status,
            headers={
                "Cache-Control": "no-store",
                "Location": location,
            },
        )

        if not import_run.is_terminal:
            response["Retry-After"] = self._retry_after_seconds()

        return response

    def retrieve(self, request, pk=None, *args, **kwargs):
        try:
            import_run = self.get_queryset().get(pk=pk)
        except DatasetImport.DoesNotExist as exc:
            raise Http404("Dataset import not found.") from exc

        if import_run.status == DatasetImportStatus.SUCCEEDED:
            self.access_service.grant_import_access(import_run.pk)
            import_run = self.import_service.get_import(import_run.pk)

        response = Response(
            DatasetImportSerializer(
                import_run,
                context=self.get_serializer_context(),
            ).data
        )
        response["Cache-Control"] = "no-store"

        if not import_run.is_terminal:
            response["Retry-After"] = self._retry_after_seconds()

        return response

    @staticmethod
    def _retry_after_seconds() -> str:
        return str(
            max(
                1,
                math.ceil(
                    settings.DATASET_IMPORT_POLL_INTERVAL_MS / 1000
                ),
            )
        )


class DatasetArtifactViewSet(
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DatasetArtifactSerializer
    permission_classes = (DatasetArtifactPermission,)
    pagination_class = None
    filter_backends = ()
    http_method_names = ("get", "head", "options")
    lookup_value_regex = (
        "[0-9a-fA-F]{8}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{12}"
    )

    def get_queryset(self):
        user = self.request.user

        if not getattr(user, "is_authenticated", False):
            return DatasetArtifact.objects.none()

        return (
            DatasetArtifact.objects.select_related(
                "dataset_version__dataset"
            )
            .filter(
                dataset_version__dataset__memberships__user=user
            )
            .distinct()
        )

    @action(detail=True, methods=("get",))
    def download(self, request, pk=None):
        artifact = self.get_object()

        try:
            url = S3ObjectStorage().presigned_download_url(
                bucket=artifact.bucket,
                object_key=artifact.object_key,
                object_version_id=artifact.object_version_id,
                filename=artifact.filename,
            )
        except ObjectStorageError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "url": url,
                "expires_in": (
                    settings.OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS
                ),
                "filename": artifact.filename,
                "checksum_sha256": artifact.checksum_sha256,
            },
            headers={"Cache-Control": "no-store"},
        )

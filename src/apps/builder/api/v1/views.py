from django.conf import settings
from django.http import Http404
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.datasets.models import (
    DatasetArtifactKind,
    DatasetMembership,
    DatasetVersionStatus,
)
from apps.datasets.storage import ObjectStorageError, S3ObjectStorage

from ...execution import BuildExecutionRejected, BuildExecutionService
from ...models import (
    AnalysisStatus,
    BuildRequest,
    BuildRun,
    DatasetAnalysis,
)
from ...tasks import (
    enqueue_analysis_for_version,
    enqueue_build_request,
    enqueue_build_run,
)
from .serializers import (
    BuildRequestCreateSerializer,
    BuildRequestSerializer,
    BuildRunSerializer,
    DatasetAnalysisSerializer,
)


class BuilderLibraryView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        memberships = DatasetMembership.objects.filter(user=request.user).select_related(
            "dataset"
        )
        items = []
        for membership in memberships:
            version = (
                membership.dataset.versions.filter(
                    status=DatasetVersionStatus.AVAILABLE
                )
                .order_by("-number")
                .first()
            )
            if version is None:
                continue
            analysis = getattr(version, "builder_analysis", None)
            if analysis is None:
                analysis = enqueue_analysis_for_version(version.pk)
            items.append(analysis)
        serializer = DatasetAnalysisSerializer(
            items,
            many=True,
            context={"request": request, "include_schema": False},
        )
        return Response({"count": len(items), "results": serializer.data})


class DatasetAnalysisViewSet(
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = DatasetAnalysisSerializer
    queryset = DatasetAnalysis.objects.none()

    def get_queryset(self):
        return (
            DatasetAnalysis.objects.filter(
                dataset_version__dataset__memberships__user=self.request.user
            )
            .select_related(
                "dataset_version",
                "dataset_version__dataset",
                "model_release",
            )
            .prefetch_related("tables__fields", "tag_predictions")
            .distinct()
        )

    @action(detail=True, methods=("post",))
    def retry(self, request, pk=None):
        analysis = self.get_object()
        if analysis.status in {AnalysisStatus.QUEUED, AnalysisStatus.RUNNING}:
            return Response(self.get_serializer(analysis).data)
        refreshed = enqueue_analysis_for_version(analysis.dataset_version_id)
        return Response(
            self.get_serializer(refreshed).data,
            status=status.HTTP_202_ACCEPTED,
        )


class BuildRequestViewSet(viewsets.ModelViewSet):
    permission_classes = (permissions.IsAuthenticated,)
    http_method_names = ("get", "post", "head", "options")
    queryset = BuildRequest.objects.none()

    def get_serializer_class(self):
        if self.action == "create":
            return BuildRequestCreateSerializer
        return BuildRequestSerializer

    def get_queryset(self):
        return (
            BuildRequest.objects.filter(user=self.request.user)
            .prefetch_related(
                "candidates__dataset_version__dataset",
                "plans__model_release",
                "plans__privacy_assessment",
                "runs__output_dataset",
                "runs__output_version",
            )
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        build_request = BuildRequest.objects.create(
            user=request.user,
            prompt=serializer.validated_data["prompt"],
            purpose=serializer.validated_data["purpose"],
            privacy_acknowledged=serializer.validated_data[
                "privacy_acknowledged"
            ],
            requested_dataset_ids=serializer.validated_data.get("dataset_ids", []),
        )
        enqueue_build_request(build_request.pk)
        build_request = self.get_queryset().get(pk=build_request.pk)
        return Response(
            BuildRequestSerializer(
                build_request,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_202_ACCEPTED,
            headers={
                "Location": self.reverse_action(
                    "detail", args=(build_request.pk,), request=request
                ),
                "Retry-After": str(max(1, settings.BUILDER_POLL_INTERVAL_MS // 1000)),
                "Cache-Control": "no-store",
            },
        )

    @action(detail=True, methods=("post",))
    def execute(self, request, pk=None):
        build_request = self.get_object()
        try:
            run, created = BuildExecutionService().create_run(
                build_request,
                request.user,
            )
        except BuildExecutionRejected as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        if created:
            enqueue_build_run(run.pk)
        return Response(
            BuildRunSerializer(run, context=self.get_serializer_context()).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
            headers={"Cache-Control": "no-store"},
        )


class BuildRunViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = BuildRunSerializer
    queryset = BuildRun.objects.none()

    def get_queryset(self):
        return BuildRun.objects.filter(requested_by=self.request.user).select_related(
            "output_dataset", "output_version", "request", "plan"
        )

    @action(detail=True, methods=("get",))
    def download(self, request, pk=None):
        run = self.get_object()
        if run.status != "succeeded" or run.output_version_id is None:
            raise Http404("The derived artifact is not available.")
        artifact = run.output_version.artifacts.filter(
            kind=DatasetArtifactKind.DATA
        ).first()
        if artifact is None:
            raise Http404("The derived artifact is missing.")
        try:
            url = S3ObjectStorage().presigned_download_url(
                bucket=artifact.bucket,
                object_key=artifact.object_key,
                object_version_id=artifact.object_version_id,
                filename=artifact.filename,
            )
        except ObjectStorageError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "url": url,
                "filename": artifact.filename,
                "expires_in": settings.OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS,
            },
            headers={"Cache-Control": "no-store"},
        )

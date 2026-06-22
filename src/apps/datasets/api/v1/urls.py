from rest_framework.routers import DefaultRouter

from .views import (
    DatasetArtifactViewSet,
    DatasetImportViewSet,
    DatasetsViewSet,
)


app_name = "datasets"

router = DefaultRouter()
router.register(
    r"imports",
    DatasetImportViewSet,
    basename="dataset-import",
)
router.register(
    r"artifacts",
    DatasetArtifactViewSet,
    basename="dataset-artifact",
)
router.register(
    r"",
    DatasetsViewSet,
    basename="dataset",
)

urlpatterns = router.urls

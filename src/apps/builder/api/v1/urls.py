from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BuilderLibraryView,
    BuildRequestViewSet,
    BuildRunViewSet,
    DatasetAnalysisViewSet,
)


app_name = "builder"
router = DefaultRouter()
router.register("analyses", DatasetAnalysisViewSet, basename="builder-analysis")
router.register("requests", BuildRequestViewSet, basename="builder-request")
router.register("runs", BuildRunViewSet, basename="builder-run")

urlpatterns = [path("library/", BuilderLibraryView.as_view(), name="library")]
urlpatterns += router.urls

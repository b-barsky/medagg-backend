from rest_framework.routers import DefaultRouter

from . import views


app_name = "search"

router = DefaultRouter()
router.register(
    r"datasets",
    views.SearchDatasetsViewSet,
    basename="search-datasets",
)

urlpatterns = router.urls

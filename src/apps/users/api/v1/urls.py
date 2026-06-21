from rest_framework.routers import DefaultRouter

from . import views

app_name = "users"

router = DefaultRouter()

router.register(r"groups", views.GroupViewSet, basename="group")
router.register(r"", views.UserViewSet, basename="user")

"""
Add aditional urls that are not part of ViewSets.
Note: Remember that every added bewlow will be part
of 'users/' url-prefix.

For example:
urlpatterns = [
    path("foo", views.BarBazView.as_view(), name="foo-bar"),
]
"""
urlpatterns = []

# Combine ViewSet's urls with others
urlpatterns += router.urls

from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "api/v1/",
        include(
            [
                path("users/", include("apps.users.api.v1.urls")),
                path("search/", include("apps.search.api.v1.urls")),
                path("datasets/", include("apps.datasets.api.v1.urls")),
                path("builder/", include("apps.builder.api.v1.urls")),
            ]
        ),
    ),
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]

from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views


app_name = "users"

urlpatterns = [
    path(
        "auth/csrf/",
        views.CsrfTokenView.as_view(),
        name="auth-csrf",
    ),
    path(
        "auth/register/",
        views.RegisterView.as_view(),
        name="auth-register",
    ),
    path(
        "auth/login/",
        views.LoginView.as_view(),
        name="auth-login",
    ),
    path(
        "auth/logout/",
        views.LogoutView.as_view(),
        name="auth-logout",
    ),
    path(
        "me/password/",
        views.ChangePasswordView.as_view(),
        name="me-password",
    ),
]

router = DefaultRouter()
router.register(r"groups", views.GroupViewSet, basename="group")
router.register(r"", views.UserViewSet, basename="user")

urlpatterns += router.urls

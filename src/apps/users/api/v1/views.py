from django.conf import settings
from django.contrib.auth import (
    login as django_login,
    logout as django_logout,
    update_session_auth_hash,
)
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.users.authentication import CsrfEnforcedSessionAuthentication
from apps.users.services import GroupService, UserService

from .serializers import (
    ChangePasswordSerializer,
    CurrentUserSerializer,
    GroupSerializer,
    LoginSerializer,
    RegistrationSerializer,
    UserSerializer,
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfTokenView(APIView):
    authentication_classes = ()
    permission_classes = (permissions.AllowAny,)

    def get(self, request):
        return Response(
            {"csrf_token": get_token(request._request)},
            headers={"Cache-Control": "no-store"},
        )


class RegisterView(APIView):
    authentication_classes = (CsrfEnforcedSessionAuthentication,)
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "registration"

    def post(self, request):
        serializer = RegistrationSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        django_login(request._request, user)
        request._request.session.set_expiry(settings.SESSION_COOKIE_AGE)

        return Response(
            {
                "user": CurrentUserSerializer(
                    user,
                    context={"request": request},
                ).data,
                "csrf_token": get_token(request._request),
            },
            status=status.HTTP_201_CREATED,
            headers={"Cache-Control": "no-store"},
        )


class LoginView(APIView):
    authentication_classes = (CsrfEnforcedSessionAuthentication,)
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        remember_me = serializer.validated_data["remember_me"]

        django_login(request._request, user)
        request._request.session.set_expiry(
            settings.SESSION_COOKIE_AGE if remember_me else 0
        )

        return Response(
            {
                "user": CurrentUserSerializer(
                    user,
                    context={"request": request},
                ).data,
                "csrf_token": get_token(request._request),
            },
            headers={"Cache-Control": "no-store"},
        )


class LogoutView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        django_logout(request._request)

        return Response(
            {
                "detail": "Signed out successfully.",
                "csrf_token": get_token(request._request),
            },
            headers={"Cache-Control": "no-store"},
        )


class ChangePasswordView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=("password",))
        update_session_auth_hash(request._request, user)

        return Response(
            {
                "detail": "Password changed successfully.",
                "csrf_token": get_token(request._request),
            },
            headers={"Cache-Control": "no-store"},
        )


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Staff-only user directory with a self-service profile endpoint.
    """

    serializer_class = UserSerializer
    permission_classes = (permissions.IsAdminUser,)

    @property
    def _user_service(self) -> UserService:
        return UserService()

    def get_queryset(self):
        return self._user_service.get_all(order_by="-date_joined")

    def get_serializer_class(self):
        if self.action == "me":
            return CurrentUserSerializer

        return super().get_serializer_class()

    @action(
        detail=False,
        methods=("get", "patch"),
        permission_classes=(permissions.IsAuthenticated,),
        url_path="me",
    )
    def me(self, request):
        if request.method == "PATCH":
            serializer = self.get_serializer(
                request.user,
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
        else:
            serializer = self.get_serializer(request.user)

        return Response(
            serializer.data,
            headers={"Cache-Control": "no-store"},
        )


class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    """Staff-only group directory."""

    serializer_class = GroupSerializer
    permission_classes = (permissions.IsAdminUser,)

    @property
    def _group_service(self) -> GroupService:
        return GroupService()

    def get_queryset(self):
        return self._group_service.get_all(order_by="name")

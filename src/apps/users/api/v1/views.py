from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.users.services import GroupService, UserService

from .serializers import GroupSerializer, UserSerializer


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Staff-only user directory.

    Authenticated users may retrieve their own profile through /me/.
    """

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

    @property
    def _user_service(self) -> UserService:
        return UserService()

    def get_queryset(self):
        return self._user_service.get_all(order_by="-date_joined")

    @action(
        detail=False,
        methods=["get"],
        permission_classes=[permissions.IsAuthenticated],
    )
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    """Staff-only group directory."""

    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAdminUser]

    @property
    def _group_service(self) -> GroupService:
        return GroupService()

    def get_queryset(self):
        return self._group_service.get_all(order_by="name")

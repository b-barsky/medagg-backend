from rest_framework.authentication import SessionAuthentication


class CsrfEnforcedSessionAuthentication(SessionAuthentication):
    """
    Apply Django's CSRF check even when the request is anonymous.

    DRF's normal SessionAuthentication only performs CSRF validation after it
    has authenticated a session user. Login and registration endpoints need
    protection from login CSRF before a session exists.
    """

    def authenticate(self, request):
        self.enforce_csrf(request)

        user = getattr(request._request, "user", None)

        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and getattr(user, "is_active", False)
        ):
            return user, None

        return None

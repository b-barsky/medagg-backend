from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.users.models import UserProfile


User = get_user_model()


class AuthenticationApiTests(APITestCase):
    csrf_url = "/api/v1/users/auth/csrf/"
    register_url = "/api/v1/users/auth/register/"
    login_url = "/api/v1/users/auth/login/"
    logout_url = "/api/v1/users/auth/logout/"
    me_url = "/api/v1/users/me/"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="researcher",
            email="researcher@example.com",
            password="A-strong-test-password-123",
            first_name="Ada",
            last_name="Lovelace",
        )

    @staticmethod
    def csrf_client() -> tuple[APIClient, str]:
        client = APIClient(enforce_csrf_checks=True)
        response = client.get(AuthenticationApiTests.csrf_url)
        return client, response.data["csrf_token"]

    def test_csrf_endpoint_sets_cookie(self):
        client = APIClient(enforce_csrf_checks=True)

        response = client.get(self.csrf_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("csrf_token", response.data)
        self.assertIn("csrftoken", response.cookies)

    def test_registration_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)

        response = client.post(
            self.register_url,
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "A-different-strong-password-123",
                "password_confirm": "A-different-strong-password-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)

        response = client.post(
            self.login_url,
            {
                "username": "researcher",
                "password": "A-strong-test-password-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_registration_creates_profile_and_session(self):
        client, csrf_token = self.csrf_client()

        response = client.post(
            self.register_url,
            {
                "username": "new-user",
                "email": "NEW@example.com",
                "first_name": "Grace",
                "last_name": "Hopper",
                "password": "A-different-strong-password-123",
                "password_confirm": "A-different-strong-password-123",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"]["username"], "new-user")
        self.assertEqual(response.data["user"]["email"], "new@example.com")
        created = User.objects.get(username="new-user")
        self.assertTrue(UserProfile.objects.filter(user=created).exists())

        me_response = client.get(self.me_url)
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertEqual(me_response.data["id"], created.pk)

    def test_login_accepts_email_and_logout_clears_session(self):
        client, csrf_token = self.csrf_client()

        login_response = client.post(
            self.login_url,
            {
                "username": "RESEARCHER@example.com",
                "password": "A-strong-test-password-123",
                "remember_me": True,
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            login_response.data["user"]["username"],
            "researcher",
        )

        logout_response = client.post(
            self.logout_url,
            {},
            format="json",
            HTTP_X_CSRFTOKEN=login_response.data["csrf_token"],
        )
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            client.get(self.me_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_logout_requires_csrf(self):
        client, csrf_token = self.csrf_client()
        login_response = client.post(
            self.login_url,
            {
                "username": "researcher",
                "password": "A-strong-test-password-123",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        response = client.post(
            self.logout_url,
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            client.get(self.me_url).status_code,
            status.HTTP_200_OK,
        )

    def test_invalid_login_does_not_reveal_identifier_state(self):
        client, csrf_token = self.csrf_client()

        response = client.post(
            self.login_url,
            {
                "username": "missing@example.com",
                "password": "wrong-password",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non_field_errors", response.data)

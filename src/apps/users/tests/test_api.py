from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase


User = get_user_model()


class UserDirectoryApiTests(APITestCase):
    users_url = "/api/v1/users/"
    groups_url = "/api/v1/users/groups/"
    me_url = "/api/v1/users/me/"

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="admin-password",
            is_staff=True,
        )
        cls.regular_user = User.objects.create_user(
            username="researcher",
            email="researcher@example.com",
            password="user-password",
        )
        cls.group = Group.objects.create(name="Researchers")

    def test_anonymous_user_cannot_list_users(self):
        response = self.client.get(self.users_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_regular_user_cannot_list_users(self):
        self.client.force_authenticate(user=self.regular_user)

        response = self.client.get(self.users_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_authenticated_user_can_retrieve_current_profile(self):
        self.client.force_authenticate(user=self.regular_user)

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.regular_user.pk)
        self.assertEqual(
            response.data["username"],
            self.regular_user.username,
        )
        self.assertIn("profile", response.data)
        self.assertIn("stats", response.data)
        self.assertIn("is_staff", response.data)

        forbidden_fields = {
            "password",
            "is_superuser",
            "groups",
            "user_permissions",
        }
        self.assertTrue(
            forbidden_fields.isdisjoint(response.data.keys())
        )

    def test_admin_receives_paginated_safe_user_list(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self.users_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertIn("results", response.data)

        expected_fields = {
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "date_joined",
        }

        for user_data in response.data["results"]:
            self.assertSetEqual(set(user_data.keys()), expected_fields)

    def test_groups_route_is_not_shadowed_by_user_detail(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self.groups_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["name"],
            "Researchers",
        )

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.models import (
    Dataset,
    DatasetMembership,
    DatasetMembershipAcquisition,
)


User = get_user_model()


class CurrentUserProfileApiTests(APITestCase):
    me_url = "/api/v1/users/me/"
    password_url = "/api/v1/users/me/password/"

    def setUp(self):
        self.user = User.objects.create_user(
            username="profile-user",
            email="profile@example.com",
            password="Original-strong-password-123",
        )
        self.client.force_authenticate(user=self.user)

    def test_profile_can_be_read_and_updated(self):
        response = self.client.patch(
            self.me_url,
            {
                "first_name": "Marie",
                "last_name": "Curie",
                "email": "marie@example.com",
                "profile": {
                    "organization": "Medagg Lab",
                    "job_title": "Researcher",
                    "location": "Berlin",
                    "website": "https://example.com",
                    "bio": "Medical dataset researcher.",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["display_name"], "Marie Curie")
        self.assertEqual(
            response.data["profile"]["organization"],
            "Medagg Lab",
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "marie@example.com")

    def test_profile_stats_include_personal_library(self):
        dataset = Dataset.objects.create(
            title="Personal dataset",
            size_bytes=2048,
        )
        DatasetMembership.objects.create(
            user=self.user,
            dataset=dataset,
            acquisition=DatasetMembershipAcquisition.IMPORTED,
        )

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["stats"]["dataset_count"], 1)
        self.assertEqual(
            response.data["stats"]["total_dataset_bytes"],
            2048,
        )

    def test_password_change_preserves_authentication(self):
        response = self.client.post(
            self.password_url,
            {
                "current_password": "Original-strong-password-123",
                "new_password": "Replacement-strong-password-456",
                "new_password_confirm": (
                    "Replacement-strong-password-456"
                ),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(
            self.user.check_password(
                "Replacement-strong-password-456"
            )
        )

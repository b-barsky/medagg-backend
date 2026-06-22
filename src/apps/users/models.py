from django.conf import settings
from django.contrib.auth import models as auth_models
from django.db import models


User = auth_models.User
Group = auth_models.Group


class UserProfile(models.Model):
    """Additional, non-authentication information for a Medagg user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    organization = models.CharField(max_length=255, blank=True)
    job_title = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    website = models.URLField(max_length=500, blank=True)
    bio = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("user_id",)

    def __str__(self) -> str:
        return f"Profile for {self.user.get_username()}"

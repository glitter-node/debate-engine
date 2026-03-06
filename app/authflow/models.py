from django.conf import settings
from django.db import models


class EmailAuthToken(models.Model):
    email = models.EmailField(max_length=254, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.email}:{self.created_at.isoformat()}"


class GoogleAccountLink(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_account",
    )
    google_sub = models.CharField(max_length=255, unique=True, db_index=True)
    email = models.EmailField(max_length=254, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"{self.google_sub}:{self.email}"

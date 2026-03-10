from django.conf import settings
from django.db import models

from ..site_roles import SITE_ROLE_CHOICES, SiteRole


class UserRole(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="site_role"
    )
    role = models.CharField(
        max_length=16, choices=SITE_ROLE_CHOICES, default=SiteRole.USER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_id}:{self.role}"

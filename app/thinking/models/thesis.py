from django.conf import settings
from django.db import models
from django.utils import timezone

from ..content_status import CONTENT_STATUS_CHOICES, ContentStatus


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)


class ActiveManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class Thesis(models.Model):
    class Stance(models.TextChoices):
        PRO = "pro"
        CON = "con"
        CONDITIONAL = "conditional"
        SUSPEND = "suspend"

    title = models.CharField(max_length=200)
    summary = models.TextField()
    stance = models.CharField(
        max_length=16, choices=Stance.choices, default=Stance.SUSPEND
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="theses"
    )
    status = models.CharField(
        max_length=32,
        choices=CONTENT_STATUS_CHOICES,
        default=ContentStatus.ACTIVE,
        db_index=True,
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    def __str__(self):
        return self.title

    @property
    def is_deleted(self) -> bool:
        return bool(self.deleted_at)

    def soft_delete(self, actor=None):
        if self.deleted_at:
            return False
        self.deleted_at = timezone.now()
        self.deleted_by = actor if getattr(actor, "is_authenticated", False) else None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
        return True

    def restore(self, actor=None):
        del actor  # kept for API symmetry with soft_delete
        if not self.deleted_at and not self.deleted_by_id:
            return False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
        return True

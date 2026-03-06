"""
app.thinking.models - Models for the "thinking" app.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .content_status import CONTENT_STATUS_CHOICES, ContentStatus
from .site_roles import SITE_ROLE_CHOICES, SiteRole


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


class Argument(models.Model):
    thesis = models.ForeignKey(
        Thesis, on_delete=models.CASCADE, related_name="arguments"
    )
    order = models.PositiveIntegerField(default=1)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]
        unique_together = [("thesis", "order")]

    def __str__(self):
        return f"A{self.order}"


class Counter(models.Model):
    thesis = models.ForeignKey(
        Thesis, on_delete=models.CASCADE, related_name="counters"
    )
    target_argument = models.ForeignKey(
        Argument, on_delete=models.CASCADE, related_name="counters"
    )
    body = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="counters"
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

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.thesis_id}->{self.target_argument_id}"

    def clean(self):
        super().clean()
        if (
            self.target_argument_id
            and self.thesis_id
            and self.target_argument.thesis_id != self.thesis_id
        ):
            raise ValidationError(
                {
                    "target_argument": "Target argument must belong to the selected thesis."
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_deleted(self) -> bool:
        return bool(self.deleted_at)

    def soft_delete(self, actor=None):
        if self.deleted_at:
            return False
        self.deleted_at = timezone.now()
        self.deleted_by = actor if getattr(actor, "is_authenticated", False) else None
        self.save(update_fields=["deleted_at", "deleted_by"])
        return True

    def restore(self, actor=None):
        del actor  # kept for API symmetry with soft_delete
        if not self.deleted_at and not self.deleted_by_id:
            return False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by"])
        return True


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


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    actor_role = models.CharField(max_length=16, blank=True, default="")
    action = models.CharField(max_length=120, db_index=True)
    target_model = models.CharField(max_length=120, blank=True, default="")
    target_id = models.CharField(max_length=64, null=True, blank=True)
    metadata = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ContentReport(models.Model):
    class TargetType(models.TextChoices):
        THESIS = "thesis", "Thesis"
        COUNTER = "counter", "Counter"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_reports",
    )
    reporter_role = models.CharField(max_length=16, blank=True, default="")
    thesis = models.ForeignKey(
        Thesis,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="content_reports",
    )
    counter = models.ForeignKey(
        Counter,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="content_reports",
    )
    reason = models.CharField(max_length=120)
    detail = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_content_reports",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["thesis", "status"]),
            models.Index(fields=["counter", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(thesis__isnull=False) & models.Q(counter__isnull=True))
                    | (models.Q(thesis__isnull=True) & models.Q(counter__isnull=False))
                ),
                name="content_report_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=["reporter", "thesis", "status"],
                name="uniq_report_per_reporter_thesis_status",
            ),
            models.UniqueConstraint(
                fields=["reporter", "counter", "status"],
                name="uniq_report_per_reporter_counter_status",
            ),
        ]

    def clean(self):
        super().clean()
        has_thesis = self.thesis_id is not None
        has_counter = self.counter_id is not None
        if has_thesis == has_counter:
            raise ValidationError(
                "ContentReport must target exactly one of thesis or counter."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def target(self):
        return self.thesis or self.counter

    @property
    def target_type(self) -> str:
        if self.thesis_id is not None:
            return "thesis"
        if self.counter_id is not None:
            return "counter"
        return ""

    @property
    def target_id(self) -> str:
        if self.thesis_id is not None:
            return str(self.thesis_id)
        if self.counter_id is not None:
            return str(self.counter_id)
        return ""

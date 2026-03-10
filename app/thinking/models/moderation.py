from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .argument import Counter
from .thesis import Thesis


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

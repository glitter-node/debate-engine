from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from ..content_status import CONTENT_STATUS_CHOICES, ContentStatus
from ..domain.chain_validator import validate_counter_parent_chain
from .thesis import ActiveManager, Thesis


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
    parent_counter = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="rebuttals",
        on_delete=models.CASCADE,
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(parent_counter__isnull=True)
                    | ~models.Q(pk=models.F("parent_counter"))
                ),
                name="counter_parent_not_self",
            )
        ]

    def __str__(self):
        return f"{self.thesis_id}->{self.target_argument_id}"

    def clean(self):
        super().clean()
        if self.parent_counter_id and self.parent_counter_id == self.id:
            raise ValidationError({"parent_counter": "Counter cannot parent itself."})
        if self.parent_counter_id:
            parent = self.parent_counter
            validate_counter_parent_chain(
                counter_id=self.id,
                parent_counter=parent,
            )
            if self.thesis_id and parent.thesis_id != self.thesis_id:
                raise ValidationError(
                    {"parent_counter": "Parent counter must belong to the same thesis."}
                )
            if (
                self.target_argument_id
                and parent.target_argument_id != self.target_argument_id
            ):
                raise ValidationError(
                    {
                        "target_argument": (
                            "Nested counter must target the same argument as parent."
                        )
                    }
                )
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

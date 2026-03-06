from __future__ import annotations

from enum import StrEnum


class ContentStatus(StrEnum):
    ACTIVE = "active"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"
    ARCHIVED = "archived"


CONTENT_STATUS_CHOICES = (
    (ContentStatus.ACTIVE, "Active"),
    (ContentStatus.PENDING_REVIEW, "Pending Review"),
    (ContentStatus.REJECTED, "Rejected"),
    (ContentStatus.ARCHIVED, "Archived"),
)

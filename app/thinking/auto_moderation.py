from __future__ import annotations

from django.db import transaction

from .audit import log_action
from .content_status import ContentStatus
from .models import ContentReport, Counter, Thesis

AUTO_REVIEW_THRESHOLD = 3
AUTO_ARCHIVE_THRESHOLD = 5


def _open_reports_count(target) -> int:
    if isinstance(target, Thesis):
        return ContentReport.objects.filter(
            thesis=target,
            status=ContentReport.Status.OPEN,
        ).count()
    if isinstance(target, Counter):
        return ContentReport.objects.filter(
            counter=target,
            status=ContentReport.Status.OPEN,
        ).count()
    return 0


def _next_status_for_count(open_count: int) -> tuple[str | None, int | None]:
    if open_count >= AUTO_ARCHIVE_THRESHOLD:
        return ContentStatus.ARCHIVED, AUTO_ARCHIVE_THRESHOLD
    if open_count >= AUTO_REVIEW_THRESHOLD:
        return ContentStatus.PENDING_REVIEW, AUTO_REVIEW_THRESHOLD
    return None, None


def maybe_auto_moderate_after_report(*, target, request=None) -> bool:
    target_model = type(target)
    if target_model not in {Thesis, Counter}:
        return False

    with transaction.atomic():
        locked = (
            target_model.all_objects.select_for_update().filter(pk=target.pk).first()
        )
        if locked is None:
            return False
        if locked.deleted_at:
            return False
        if locked.status == ContentStatus.REJECTED:
            return False

        open_count = _open_reports_count(locked)
        desired_status, threshold = _next_status_for_count(open_count)
        if not desired_status:
            return False

        old_status = locked.status
        if desired_status == ContentStatus.PENDING_REVIEW:
            if old_status != ContentStatus.ACTIVE:
                return False
        elif desired_status == ContentStatus.ARCHIVED:
            if old_status not in {ContentStatus.ACTIVE, ContentStatus.PENDING_REVIEW}:
                return False

        if old_status == desired_status:
            return False

        locked.status = desired_status
        locked.save(update_fields=["status"])
        log_action(
            actor=None,
            action="moderation.auto_status_change",
            target=locked,
            metadata={
                "target_type": (
                    ContentReport.TargetType.THESIS
                    if isinstance(locked, Thesis)
                    else ContentReport.TargetType.COUNTER
                ),
                "target_id": str(locked.pk),
                "old_status": old_status,
                "new_status": desired_status,
                "open_report_count": open_count,
                "threshold_triggered": threshold,
            },
            request=request,
        )
        return True

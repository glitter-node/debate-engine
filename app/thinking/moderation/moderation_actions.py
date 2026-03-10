from django.db import transaction
from django.utils import timezone

from ..models import ContentReport


def apply_report_status(*, report, next_status, actor):
    if report.status != ContentReport.Status.OPEN:
        return False
    report.status = next_status
    report.resolved_at = timezone.now()
    report.resolved_by = actor
    report.save(update_fields=["status", "resolved_at", "resolved_by"])
    return True


def bulk_apply_report_status(*, report_ids, next_status, actor):
    updated = []
    with transaction.atomic():
        locked_reports = list(
            ContentReport.objects.select_for_update()
            .filter(id__in=report_ids)
            .order_by("id")
        )
        for report in locked_reports:
            if not apply_report_status(report=report, next_status=next_status, actor=actor):
                continue
            updated.append(report)
    return updated

from django.db.models import Count
from django.urls import reverse

from ..moderation_metrics import ESCALATION_LEVEL_1, escalation_level_for_count
from ..models import ContentReport, Counter, Thesis


def _report_target_key(report):
    if report.thesis_id is not None:
        return (ContentReport.TargetType.THESIS, report.thesis_id)
    if report.counter_id is not None:
        return (ContentReport.TargetType.COUNTER, report.counter_id)
    return ("", 0)


def build_report_rows(reports, stale_cutoff=None):
    if not reports:
        return []

    thesis_ids = sorted({report.thesis_id for report in reports if report.thesis_id})
    counter_ids = sorted({report.counter_id for report in reports if report.counter_id})

    open_count_map = {}
    if thesis_ids:
        for row in (
            ContentReport.objects.filter(
                status=ContentReport.Status.OPEN,
                thesis_id__in=thesis_ids,
            )
            .values("thesis_id")
            .annotate(open_count=Count("id"))
        ):
            open_count_map[(ContentReport.TargetType.THESIS, row["thesis_id"])] = row[
                "open_count"
            ]
    if counter_ids:
        for row in (
            ContentReport.objects.filter(
                status=ContentReport.Status.OPEN,
                counter_id__in=counter_ids,
            )
            .values("counter_id")
            .annotate(open_count=Count("id"))
        ):
            open_count_map[(ContentReport.TargetType.COUNTER, row["counter_id"])] = row[
                "open_count"
            ]

    thesis_map = {
        row["id"]: row
        for row in Thesis.all_objects.filter(pk__in=thesis_ids).values(
            "id", "title", "status", "deleted_at"
        )
    }
    counter_map = {
        row["id"]: row
        for row in Counter.all_objects.filter(pk__in=counter_ids).values(
            "id", "thesis_id", "status", "deleted_at"
        )
    }

    rows = []
    for report in reports:
        target_url = ""
        target_label = f"{report.target_type} #{report.target_id}"
        target_status = ""
        target_deleted = False
        key = _report_target_key(report)
        open_count = open_count_map.get(key, 0)
        escalation_level = escalation_level_for_count(open_count)
        is_auto_moderated = open_count >= ESCALATION_LEVEL_1
        is_stale = bool(
            stale_cutoff is not None
            and report.status == ContentReport.Status.OPEN
            and report.created_at < stale_cutoff
        )

        if report.target_type == ContentReport.TargetType.THESIS:
            thesis = thesis_map.get(report.thesis_id)
            if thesis:
                target_label = thesis["title"]
                target_status = thesis["status"]
                target_deleted = bool(thesis["deleted_at"])
                target_url = reverse("thinking:thesis_detail", kwargs={"pk": thesis["id"]})
        elif report.target_type == ContentReport.TargetType.COUNTER:
            counter = counter_map.get(report.counter_id)
            if counter:
                target_status = counter["status"]
                target_deleted = bool(counter["deleted_at"])
                target_label = f"Counter #{counter['id']}"
                target_url = (
                    reverse("thinking:thesis_detail", kwargs={"pk": counter["thesis_id"]})
                    + f"#counter-{counter['id']}"
                )

        rows.append(
            {
                "report": report,
                "target_label": target_label,
                "target_url": target_url,
                "target_status": target_status,
                "target_deleted": target_deleted,
                "open_count_for_target": open_count,
                "escalation_level": escalation_level,
                "is_auto_moderated": is_auto_moderated,
                "is_stale": is_stale,
            }
        )
    return rows

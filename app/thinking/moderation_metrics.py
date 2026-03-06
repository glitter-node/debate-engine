from __future__ import annotations

from datetime import timedelta
import math
import os

from django.db.models import Count, DurationField, ExpressionWrapper, F, Min, Q
from django.utils import timezone

from .auto_moderation import AUTO_REVIEW_THRESHOLD
from .models import ContentReport

DEFAULT_SINCE_DAYS = 30
ALLOWED_SINCE_DAYS = (7, 30, 90)
MAX_LATENCY_SAMPLES = 5000
MAX_OPERATOR_LATENCY_SAMPLES = 5000
DEFAULT_STALE_OPEN_HOURS = 48
MIN_STALE_OPEN_HOURS = 1
MAX_STALE_OPEN_HOURS = 720
ESCALATION_LEVEL_1 = 3
ESCALATION_LEVEL_2 = 5
ESCALATION_LEVEL_3 = 10


def _percentile(sorted_values: list[int], percentile: float) -> int | None:
    if not sorted_values:
        return None
    rank = max(
        0, min(len(sorted_values) - 1, math.ceil(percentile * len(sorted_values)) - 1)
    )
    return sorted_values[rank]


def _duration_label(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def get_stale_open_hours() -> int:
    raw_value = os.getenv("MODERATION_STALE_OPEN_HOURS", "").strip()
    try:
        parsed = int(raw_value) if raw_value else DEFAULT_STALE_OPEN_HOURS
    except (TypeError, ValueError):
        parsed = DEFAULT_STALE_OPEN_HOURS
    return max(MIN_STALE_OPEN_HOURS, min(MAX_STALE_OPEN_HOURS, parsed))


def escalation_level_for_count(open_count: int) -> int:
    if open_count >= ESCALATION_LEVEL_3:
        return 3
    if open_count >= ESCALATION_LEVEL_2:
        return 2
    if open_count >= ESCALATION_LEVEL_1:
        return 1
    return 0


def escalation_min_count_for_level(level: int) -> int:
    if level >= 3:
        return ESCALATION_LEVEL_3
    if level == 2:
        return ESCALATION_LEVEL_2
    if level == 1:
        return ESCALATION_LEVEL_1
    return 0


def build_moderation_metrics(
    *, since_days: int, operator_username: str = "", now=None
) -> dict:
    if since_days not in ALLOWED_SINCE_DAYS:
        since_days = DEFAULT_SINCE_DAYS
    now = now or timezone.now()
    window_start = now - timedelta(days=since_days)
    window_qs = ContentReport.objects.filter(created_at__gte=window_start)

    counts = window_qs.aggregate(
        open_count=Count("id", filter=Q(status=ContentReport.Status.OPEN)),
        resolved_count=Count("id", filter=Q(status=ContentReport.Status.RESOLVED)),
        dismissed_count=Count("id", filter=Q(status=ContentReport.Status.DISMISSED)),
    )

    decisions_qs = window_qs.filter(
        status__in=[ContentReport.Status.RESOLVED, ContentReport.Status.DISMISSED],
        resolved_at__isnull=False,
    )
    decision_total = decisions_qs.count()
    decision_duration = ExpressionWrapper(
        F("resolved_at") - F("created_at"),
        output_field=DurationField(),
    )
    duration_values = list(
        decisions_qs.annotate(decision_duration=decision_duration)
        .order_by("-resolved_at")
        .values_list("decision_duration", flat=True)[:MAX_LATENCY_SAMPLES]
    )
    duration_seconds = sorted(
        max(0, int(duration.total_seconds()))
        for duration in duration_values
        if duration is not None
    )
    median_seconds = _percentile(duration_seconds, 0.5)
    p90_seconds = _percentile(duration_seconds, 0.9)
    sampled = decision_total > len(duration_seconds)

    operator_username = (operator_username or "").strip()
    operator_filter_applied = bool(operator_username)
    operator_decisions_qs = decisions_qs.filter(resolved_by__isnull=False)
    if operator_filter_applied:
        operator_decisions_qs = operator_decisions_qs.filter(
            resolved_by__username=operator_username
        )
    operator_rows = list(
        operator_decisions_qs.values("resolved_by_id", "resolved_by__username")
        .annotate(
            decision_count=Count("id"),
            resolved_count=Count("id", filter=Q(status=ContentReport.Status.RESOLVED)),
            dismissed_count=Count(
                "id", filter=Q(status=ContentReport.Status.DISMISSED)
            ),
        )
        .order_by("-decision_count", "resolved_by__username")
    )
    operator_latency_rows = list(
        operator_decisions_qs.values(
            "resolved_by__username",
            "created_at",
            "resolved_at",
        ).order_by("-resolved_at")[:MAX_OPERATOR_LATENCY_SAMPLES]
    )
    operator_latency_samples: dict[str, list[int]] = {}
    for row in operator_latency_rows:
        username = row["resolved_by__username"] or "Unknown"
        created_at = row["created_at"]
        resolved_at = row["resolved_at"]
        if not created_at or not resolved_at:
            continue
        seconds = max(0, int((resolved_at - created_at).total_seconds()))
        operator_latency_samples.setdefault(username, []).append(seconds)

    operator_decisions = [
        {
            "operator_id": row["resolved_by_id"],
            "operator_username": row["resolved_by__username"] or "Unknown",
            "decision_count": row["decision_count"],
        }
        for row in operator_rows
    ]
    operator_metrics = []
    for row in operator_rows:
        username = row["resolved_by__username"] or "Unknown"
        sample_seconds = sorted(operator_latency_samples.get(username, []))
        median_operator_seconds = _percentile(sample_seconds, 0.5)
        p90_operator_seconds = _percentile(sample_seconds, 0.9)
        operator_metrics.append(
            {
                "operator_id": row["resolved_by_id"],
                "operator_username": username,
                "decisions_count": row["decision_count"],
                "resolved_count": row["resolved_count"],
                "dismissed_count": row["dismissed_count"],
                "median_seconds": median_operator_seconds,
                "median_label": _duration_label(median_operator_seconds),
                "p90_seconds": p90_operator_seconds,
                "p90_label": _duration_label(p90_operator_seconds),
                "sample_count": len(sample_seconds),
                "sampled_flag": row["decision_count"] > len(sample_seconds),
            }
        )
    operator_not_found = operator_filter_applied and not operator_metrics

    open_thesis_rows = list(
        window_qs.filter(
            status=ContentReport.Status.OPEN,
            thesis__isnull=False,
        )
        .values("thesis_id")
        .annotate(open_count=Count("id"))
    )
    open_counter_rows = list(
        window_qs.filter(
            status=ContentReport.Status.OPEN,
            counter__isnull=False,
        )
        .values("counter_id")
        .annotate(open_count=Count("id"))
    )
    open_target_rows = [
        {
            "target_type": ContentReport.TargetType.THESIS,
            "open_count": row["open_count"],
        }
        for row in open_thesis_rows
    ] + [
        {
            "target_type": ContentReport.TargetType.COUNTER,
            "open_count": row["open_count"],
        }
        for row in open_counter_rows
    ]
    hot_targets = [
        row for row in open_target_rows if row["open_count"] >= AUTO_REVIEW_THRESHOLD
    ]
    escalation_distribution = {
        "level_1_targets": 0,
        "level_2_targets": 0,
        "level_3_targets": 0,
    }
    max_escalation_level = 0
    for row in open_target_rows:
        level = escalation_level_for_count(row["open_count"])
        max_escalation_level = max(max_escalation_level, level)
        if level >= 1:
            escalation_distribution["level_1_targets"] += 1
        if level >= 2:
            escalation_distribution["level_2_targets"] += 1
        if level >= 3:
            escalation_distribution["level_3_targets"] += 1
    stale_threshold_hours = get_stale_open_hours()
    stale_cutoff = now - timedelta(hours=stale_threshold_hours)
    stale_agg = window_qs.filter(
        status=ContentReport.Status.OPEN,
        created_at__lt=stale_cutoff,
    ).aggregate(stale_open_count=Count("id"), oldest_created_at=Min("created_at"))
    oldest_created_at = stale_agg["oldest_created_at"]
    oldest_stale_age_seconds = None
    if oldest_created_at is not None:
        oldest_stale_age_seconds = max(
            0, int((now - oldest_created_at).total_seconds())
        )

    return {
        "since_days": since_days,
        "window_start": window_start,
        "window_end": now,
        "counts_by_status": counts,
        "latency": {
            "sample_size": len(duration_seconds),
            "sampled": sampled,
            "median_seconds": median_seconds,
            "median_label": _duration_label(median_seconds),
            "p90_seconds": p90_seconds,
            "p90_label": _duration_label(p90_seconds),
        },
        "operator_decisions": operator_decisions,
        "operator_metrics": operator_metrics,
        "operator_filter": operator_username,
        "operator_filter_applied": operator_filter_applied,
        "operator_not_found": operator_not_found,
        "operator_latency_sample_cap": MAX_OPERATOR_LATENCY_SAMPLES,
        "hot_targets_count": len(hot_targets),
        "hot_reports_total": sum(row["open_count"] for row in hot_targets),
        "auto_review_threshold": AUTO_REVIEW_THRESHOLD,
        "stale_threshold_hours": stale_threshold_hours,
        "stale_open_count": stale_agg["stale_open_count"],
        "oldest_stale_open_age_seconds": oldest_stale_age_seconds,
        "escalation_distribution": escalation_distribution,
        "max_escalation_level": max_escalation_level,
    }

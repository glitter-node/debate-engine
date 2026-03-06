"""
app.thinking.views - Views for the "thinking" app.
"""

import csv
from datetime import timedelta
from io import StringIO

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView
from django.views.decorators.http import require_GET, require_POST

from api.middleware.ip_resolver import get_client_ip

from .auto_moderation import maybe_auto_moderate_after_report
from .audit import log_action
from .content_status import CONTENT_STATUS_CHOICES, ContentStatus
from .forms import ArgumentFormSet, CounterForm, ThesisForm
from .moderation_metrics import (
    ALLOWED_SINCE_DAYS,
    DEFAULT_SINCE_DAYS,
    ESCALATION_LEVEL_1,
    build_moderation_metrics,
    escalation_level_for_count,
    escalation_min_count_for_level,
    get_stale_open_hours,
)
from .models import Argument, ContentReport, Counter, Thesis
from .report_rate_limit import allow_report_submit
from .roles import (
    RoleRequiredMixin,
    ensure_user_role,
    role_required,
    user_has_site_role,
)
from .site_roles import SiteRole

MODERATION_ROLES = (SiteRole.MODERATOR, SiteRole.OPERATOR)
MODERATION_PAGE_SIZE = 50
MAX_PRIORITY_SORT_CANDIDATES = 2000
MODERATION_TABS = ("reports", "metrics")
REPORT_REASONS = (
    "spam",
    "harassment",
    "hate",
    "off_topic",
    "misinformation",
    "other",
)
STATUS_TRANSITIONS = {
    ContentStatus.ACTIVE: {ContentStatus.ARCHIVED, ContentStatus.REJECTED},
    ContentStatus.PENDING_REVIEW: {ContentStatus.ACTIVE},
    ContentStatus.REJECTED: {ContentStatus.ACTIVE},
}


def _can_include_inactive(request) -> bool:
    return bool(
        request.GET.get("include_inactive") == "1"
        and user_has_site_role(request.user, *MODERATION_ROLES)
    )


def _can_include_deleted(request) -> bool:
    return bool(
        request.GET.get("include_deleted") == "1"
        and user_has_site_role(request.user, *MODERATION_ROLES)
    )


def _allowed_next_statuses(status: str):
    return sorted(STATUS_TRANSITIONS.get(status, set()))


def _parse_since_days(raw_value: str) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_SINCE_DAYS
    if parsed in ALLOWED_SINCE_DAYS:
        return parsed
    return DEFAULT_SINCE_DAYS


def _report_return_url_for(target) -> str:
    if isinstance(target, Thesis):
        return reverse("thinking:thesis_detail", kwargs={"pk": target.pk})
    if isinstance(target, Counter):
        return reverse("thinking:thesis_detail", kwargs={"pk": target.thesis_id})
    raise Http404


def _report_target_key(report):
    if report.thesis_id is not None:
        return (ContentReport.TargetType.THESIS, report.thesis_id)
    if report.counter_id is not None:
        return (ContentReport.TargetType.COUNTER, report.counter_id)
    return ("", 0)


def _build_report_rows(reports, stale_cutoff=None):
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
                target_url = reverse(
                    "thinking:thesis_detail", kwargs={"pk": thesis["id"]}
                )
        elif report.target_type == ContentReport.TargetType.COUNTER:
            counter = counter_map.get(report.counter_id)
            if counter:
                target_status = counter["status"]
                target_deleted = bool(counter["deleted_at"])
                target_label = f"Counter #{counter['id']}"
                target_url = (
                    reverse(
                        "thinking:thesis_detail", kwargs={"pk": counter["thesis_id"]}
                    )
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


class HomeView(TemplateView):
    template_name = "thinking/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        include_inactive = _can_include_inactive(self.request)
        include_deleted = _can_include_deleted(self.request)
        cached = None
        if not include_inactive and not include_deleted:
            cached = cache.get("thinking:home:lists")
        if cached is None:
            manager = Thesis.all_objects if include_deleted else Thesis.objects
            base_qs = manager.select_related("author")
            if not include_inactive:
                base_qs = base_qs.filter(status=ContentStatus.ACTIVE)
            cached = {
                "recent_theses": list(base_qs.order_by("-created_at")[:6]),
                "active_theses": list(
                    base_qs.annotate(counter_count=Count("counters")).order_by(
                        "-counter_count", "-updated_at"
                    )[:6]
                ),
                "unanswered_theses": list(
                    base_qs.annotate(counter_count=Count("counters"))
                    .filter(counter_count=0)
                    .order_by("-created_at")[:6]
                ),
            }
            if not include_inactive and not include_deleted:
                cache.set("thinking:home:lists", cached, timeout=30)
        ctx.update(cached)
        ctx["include_inactive"] = include_inactive
        ctx["include_deleted"] = include_deleted
        return ctx


class ThesisListView(ListView):
    model = Thesis
    template_name = "thinking/thesis_list.html"
    context_object_name = "theses"
    paginate_by = 20

    def get_queryset(self):
        sort = self.request.GET.get("sort", "active")
        include_deleted = _can_include_deleted(self.request)
        manager = Thesis.all_objects if include_deleted else Thesis.objects
        qs = manager.select_related("author").annotate(counter_count=Count("counters"))
        if not _can_include_inactive(self.request):
            qs = qs.filter(status=ContentStatus.ACTIVE)
        if sort == "new":
            return qs.order_by("-created_at")
        if sort == "unanswered":
            return qs.filter(counter_count=0).order_by("-created_at")
        return qs.order_by("-counter_count", "-updated_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["include_inactive"] = _can_include_inactive(self.request)
        ctx["include_deleted"] = _can_include_deleted(self.request)
        return ctx


class ThesisDetailView(DetailView):
    model = Thesis
    template_name = "thinking/thesis_detail.html"
    context_object_name = "thesis"

    def get_queryset(self):
        can_moderate = user_has_site_role(self.request.user, *MODERATION_ROLES)
        include_deleted = _can_include_deleted(self.request)
        counter_manager = (
            Counter.all_objects
            if (can_moderate and include_deleted)
            else Counter.objects
        )
        counters_qs = counter_manager.select_related("author")
        if not can_moderate:
            counters_qs = counters_qs.filter(status=ContentStatus.ACTIVE)
        arguments_qs = Argument.objects.prefetch_related(
            Prefetch("counters", queryset=counters_qs)
        )
        thesis_manager = (
            Thesis.all_objects if (can_moderate and include_deleted) else Thesis.objects
        )
        qs = thesis_manager.select_related("author").prefetch_related(
            Prefetch("arguments", queryset=arguments_qs)
        )
        if not can_moderate:
            qs = qs.filter(status=ContentStatus.ACTIVE)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        thesis = ctx["thesis"]
        arguments = list(thesis.arguments.all())
        counters_by_argument = {a.id: list(a.counters.all()) for a in arguments}
        ctx["arguments"] = arguments
        ctx["counters_by_argument"] = counters_by_argument
        ctx["can_moderate"] = user_has_site_role(self.request.user, *MODERATION_ROLES)
        ctx["include_deleted"] = _can_include_deleted(self.request)
        ctx["status_choices"] = CONTENT_STATUS_CHOICES
        ctx["thesis_next_statuses"] = _allowed_next_statuses(thesis.status)
        ctx["counter_next_statuses"] = {
            c.id: _allowed_next_statuses(c.status)
            for counters in counters_by_argument.values()
            for c in counters
        }
        if self.request.user.is_authenticated:
            open_reports_qs = ContentReport.objects.filter(
                reporter=self.request.user,
                status=ContentReport.Status.OPEN,
            )
            thesis_open = open_reports_qs.filter(thesis=thesis).exists()
            counter_open_ids = set(
                int(counter_id)
                for counter_id in open_reports_qs.filter(
                    counter_id__in=[
                        c.id
                        for counters in counters_by_argument.values()
                        for c in counters
                    ],
                ).values_list("counter_id", flat=True)
            )
        else:
            thesis_open = False
            counter_open_ids = set()
        ctx["report_reason_choices"] = REPORT_REASONS
        ctx["thesis_report_open"] = thesis_open
        ctx["counter_report_open_ids"] = counter_open_ids
        return ctx


class ThesisCreateView(LoginRequiredMixin, FormView):
    template_name = "thinking/thesis_create.html"
    form_class = ThesisForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "argument_formset" not in ctx:
            ctx["argument_formset"] = ArgumentFormSet()
        return ctx

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        argument_formset = ArgumentFormSet(request.POST)
        if form.is_valid() and argument_formset.is_valid():
            thesis = form.save(commit=False)
            thesis.author = request.user
            thesis.save()
            argument_formset.instance = thesis
            arguments = argument_formset.save(commit=False)
            cleaned = [a for a in arguments if (a.body or "").strip()]
            if not cleaned:
                thesis.delete()
                return self.form_invalid(form)
            for a in cleaned:
                a.thesis = thesis
                a.save()
            cache.delete("thinking:home:lists")
            return redirect("thinking:thesis_detail", pk=thesis.pk)
        return self.render_to_response(
            self.get_context_data(form=form, argument_formset=argument_formset)
        )


class CounterCreateView(LoginRequiredMixin, FormView):
    template_name = "thinking/counter_create.html"
    form_class = CounterForm

    def dispatch(self, request, *args, **kwargs):
        self.thesis = Thesis.objects.filter(pk=kwargs.get("pk")).first()
        if not self.thesis:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["target_argument"].queryset = self.thesis.arguments.all()
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["thesis"] = self.thesis
        return ctx

    def form_valid(self, form):
        counter = form.save(commit=False)
        counter.thesis = self.thesis
        counter.author = self.request.user
        counter.save()
        cache.delete("thinking:home:lists")
        return redirect("thinking:thesis_detail", pk=self.thesis.pk)


@role_required(*MODERATION_ROLES)
def moderation_panel(request):
    log_action(
        actor=request.user,
        action="moderation.access",
        metadata={"path": request.path},
        request=request,
    )
    active_tab = request.GET.get("tab", "reports").strip().lower()
    if active_tab not in MODERATION_TABS:
        active_tab = "reports"
    since_days = _parse_since_days(request.GET.get("since_days"))
    if active_tab == "metrics":
        operator_filter = request.GET.get("operator", "").strip()
        metrics = build_moderation_metrics(
            since_days=since_days,
            operator_username=operator_filter,
        )
        context = {
            "active_tab": active_tab,
            "since_days": since_days,
            "since_days_options": ALLOWED_SINCE_DAYS,
            "operator_filter": operator_filter,
            "metrics": metrics,
        }
        return render(request, "thinking/moderation.html", context)

    status = request.GET.get("status", ContentReport.Status.OPEN).strip().lower()
    if status not in {
        ContentReport.Status.OPEN,
        ContentReport.Status.RESOLVED,
        ContentReport.Status.DISMISSED,
    }:
        status = ContentReport.Status.OPEN
    target_type = request.GET.get("target_type", "").strip().lower()
    if target_type not in {
        "",
        ContentReport.TargetType.THESIS,
        ContentReport.TargetType.COUNTER,
    }:
        target_type = ""
    reason = request.GET.get("reason", "").strip().lower()
    if reason and reason not in REPORT_REASONS:
        reason = ""
    only_auto = request.GET.get("only_auto") == "1"
    stale_only = request.GET.get("stale_only") == "1"
    sort_mode = request.GET.get("sort", "").strip().lower()
    if sort_mode not in {"", "priority"}:
        sort_mode = ""
    escalation_level = request.GET.get("escalation_level", "").strip()
    if escalation_level in {"1", "2", "3"}:
        escalation_level = int(escalation_level)
    else:
        escalation_level = None
    stale_threshold_hours = get_stale_open_hours()
    stale_cutoff = timezone.now() - timedelta(hours=stale_threshold_hours)
    if stale_only:
        status = ContentReport.Status.OPEN

    base_qs = ContentReport.objects.filter(status=status).select_related(
        "reporter",
        "thesis",
        "counter",
        "counter__thesis",
    )
    if target_type:
        if target_type == ContentReport.TargetType.THESIS:
            base_qs = base_qs.filter(thesis__isnull=False)
        elif target_type == ContentReport.TargetType.COUNTER:
            base_qs = base_qs.filter(counter__isnull=False)
    reason_choices = list(
        base_qs.order_by().values_list("reason", flat=True).distinct()
    )
    if reason:
        base_qs = base_qs.filter(reason=reason)
    if stale_only:
        base_qs = base_qs.filter(created_at__lt=stale_cutoff)

    required_open_count = 0
    if only_auto:
        required_open_count = ESCALATION_LEVEL_1
    if escalation_level:
        required_open_count = max(
            required_open_count,
            escalation_min_count_for_level(escalation_level),
        )
    if required_open_count:
        open_filter = Q(status=ContentReport.Status.OPEN)
        if target_type:
            if target_type == ContentReport.TargetType.THESIS:
                open_filter &= Q(thesis__isnull=False)
            elif target_type == ContentReport.TargetType.COUNTER:
                open_filter &= Q(counter__isnull=False)
        open_reports_qs = ContentReport.objects.filter(open_filter)
        thesis_targets = [
            row["thesis_id"]
            for row in open_reports_qs.filter(thesis__isnull=False)
            .values("thesis_id")
            .annotate(open_count=Count("id"))
            .filter(open_count__gte=required_open_count)
        ]
        counter_targets = [
            row["counter_id"]
            for row in open_reports_qs.filter(counter__isnull=False)
            .values("counter_id")
            .annotate(open_count=Count("id"))
            .filter(open_count__gte=required_open_count)
        ]
        if thesis_targets or counter_targets:
            base_qs = base_qs.filter(
                Q(thesis_id__in=thesis_targets) | Q(counter_id__in=counter_targets)
            )
        else:
            base_qs = base_qs.none()

    priority_mode = sort_mode == "priority"
    priority_candidates_capped = False
    if priority_mode:
        candidate_qs = base_qs.order_by("created_at", "id")
        candidates = list(candidate_qs[: MAX_PRIORITY_SORT_CANDIDATES + 1])
        if len(candidates) > MAX_PRIORITY_SORT_CANDIDATES:
            priority_candidates_capped = True
            candidates = candidates[:MAX_PRIORITY_SORT_CANDIDATES]
        report_rows = _build_report_rows(candidates, stale_cutoff=stale_cutoff)
        report_rows = sorted(
            report_rows,
            key=lambda row: (
                -row["escalation_level"],
                -int(row["is_stale"]),
                row["report"].created_at,
                row["report"].id,
            ),
        )
        paginator = Paginator(report_rows, MODERATION_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page", 1))
        report_rows_page = list(page_obj.object_list)
    else:
        ordered_qs = base_qs.order_by("-created_at", "-id")
        paginator = Paginator(ordered_qs, MODERATION_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page", 1))
        report_rows_page = _build_report_rows(
            list(page_obj.object_list), stale_cutoff=stale_cutoff
        )

    base_query = request.GET.copy()
    base_query.pop("page", None)
    query_string = base_query.urlencode()
    context = {
        "active_tab": active_tab,
        "since_days": since_days,
        "since_days_options": ALLOWED_SINCE_DAYS,
        "open_reports": report_rows_page,
        "open_reports_count": paginator.count,
        "status_filter": status,
        "target_type_filter": target_type,
        "reason_filter": reason,
        "reason_choices": reason_choices,
        "only_auto_filter": only_auto,
        "stale_only_filter": stale_only,
        "sort_filter": sort_mode,
        "escalation_level_filter": escalation_level,
        "priority_mode": priority_mode,
        "priority_candidates_capped": priority_candidates_capped,
        "priority_candidates_cap": MAX_PRIORITY_SORT_CANDIDATES,
        "stale_threshold_hours": stale_threshold_hours,
        "page_obj": page_obj,
        "query_string": query_string,
        "full_query_string": request.GET.urlencode(),
    }
    return render(request, "thinking/moderation.html", context)


@require_GET
@role_required(*MODERATION_ROLES)
def moderation_metrics_csv(request):
    since_days = _parse_since_days(request.GET.get("since_days"))
    operator_filter = request.GET.get("operator", "").strip()
    generated_at = timezone.now()
    metrics = build_moderation_metrics(
        since_days=since_days,
        operator_username=operator_filter,
        now=generated_at,
    )

    rows_buffer = StringIO()
    writer = csv.writer(rows_buffer)

    writer.writerow(["GeneratedAt", "SinceDays", "OperatorFilter"])
    writer.writerow([generated_at.isoformat(), since_days, operator_filter])
    writer.writerow([])

    writer.writerow(["Status counts"])
    writer.writerow(["Metric", "Value"])
    writer.writerow(["open_count", metrics["counts_by_status"]["open_count"]])
    writer.writerow(["resolved_count", metrics["counts_by_status"]["resolved_count"]])
    writer.writerow(["dismissed_count", metrics["counts_by_status"]["dismissed_count"]])
    writer.writerow([])

    writer.writerow(["Latency (overall)"])
    writer.writerow(["Metric", "Value"])
    writer.writerow(["latency_median_seconds", metrics["latency"]["median_seconds"]])
    writer.writerow(["latency_p90_seconds", metrics["latency"]["p90_seconds"]])
    writer.writerow(["latency_sample_count", metrics["latency"]["sample_size"]])
    writer.writerow(["latency_sampled_flag", metrics["latency"]["sampled"]])
    writer.writerow([])

    writer.writerow(["Stale open"])
    writer.writerow(["Metric", "Value"])
    writer.writerow(["stale_open_hours_threshold", metrics["stale_threshold_hours"]])
    writer.writerow(["stale_open_count", metrics["stale_open_count"]])
    writer.writerow(
        ["oldest_stale_age_seconds", metrics["oldest_stale_open_age_seconds"] or ""]
    )
    writer.writerow([])

    writer.writerow(["Escalation"])
    writer.writerow(["Level", "TargetsCount"])
    writer.writerow(["0", ""])
    writer.writerow(["1", metrics["escalation_distribution"]["level_1_targets"]])
    writer.writerow(["2", metrics["escalation_distribution"]["level_2_targets"]])
    writer.writerow(["3", metrics["escalation_distribution"]["level_3_targets"]])
    writer.writerow(["max", metrics["max_escalation_level"]])
    writer.writerow([])

    writer.writerow(["Hot targets pressure"])
    writer.writerow(["Metric", "Value"])
    writer.writerow(["hot_targets_count", metrics["hot_targets_count"]])
    writer.writerow(["hot_reports_total", metrics["hot_reports_total"]])
    writer.writerow([])

    writer.writerow(["Operator workload"])
    writer.writerow(
        [
            "Operator",
            "Decisions",
            "Resolved",
            "Dismissed",
            "MedianSeconds",
            "P90Seconds",
            "SampleCount",
            "SampledFlag",
        ]
    )
    for row in metrics["operator_metrics"]:
        writer.writerow(
            [
                row["operator_username"],
                row["decisions_count"],
                row["resolved_count"],
                row["dismissed_count"],
                row["median_seconds"] if row["median_seconds"] is not None else "",
                row["p90_seconds"] if row["p90_seconds"] is not None else "",
                row["sample_count"],
                row["sampled_flag"],
            ]
        )

    filename = f"moderation_metrics_{since_days}d_{generated_at.strftime('%Y%m%d')}.csv"
    response = HttpResponse(
        rows_buffer.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_POST
@role_required(*MODERATION_ROLES)
def moderation_mark_reviewed(request):
    target = None
    thesis_id = request.POST.get("thesis_id")
    if thesis_id:
        target = Thesis.objects.filter(pk=thesis_id).first()
    log_action(
        actor=request.user,
        action="moderation.mark_reviewed",
        target=target,
        metadata={"thesis_id": thesis_id or ""},
        request=request,
    )
    return redirect("thinking:moderation_panel")


class _ReportCreateView(LoginRequiredMixin, View):
    model = None
    target_type = None

    def get_target(self, **kwargs):
        return get_object_or_404(self.model.objects, pk=kwargs.get("pk"))

    def post(self, request, *args, **kwargs):
        target = self.get_target(**kwargs)
        return_url = _report_return_url_for(target)
        if not allow_report_submit(getattr(request.user, "id", None)):
            return redirect(return_url)

        reason = request.POST.get("reason", "").strip().lower()
        if reason not in REPORT_REASONS:
            reason = "other"
        detail = request.POST.get("detail", "").strip()
        role_obj = ensure_user_role(request.user)
        reporter_role = role_obj.role if role_obj else ""
        target_fk_name = (
            "thesis"
            if self.target_type == ContentReport.TargetType.THESIS
            else "counter"
        )
        target_lookup = {target_fk_name: target}
        report_kwargs = {
            "reporter": request.user,
            "reporter_role": reporter_role,
            "reason": reason,
            "detail": detail,
            "ip_address": get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            **target_lookup,
        }
        created = False
        try:
            _, created = ContentReport.objects.get_or_create(
                reporter=request.user,
                status=ContentReport.Status.OPEN,
                defaults=report_kwargs,
                **target_lookup,
            )
        except IntegrityError:
            created = False
        if created:
            log_action(
                actor=request.user,
                action="content.report_submitted",
                target=target,
                metadata={
                    "target_type": self.target_type,
                    "target_id": str(target.pk),
                    "reason": reason,
                },
                request=request,
            )
        maybe_auto_moderate_after_report(
            target=target,
            request=request,
        )
        return redirect(return_url)


class ThesisReportCreateView(_ReportCreateView):
    model = Thesis
    target_type = ContentReport.TargetType.THESIS


class CounterReportCreateView(_ReportCreateView):
    model = Counter
    target_type = ContentReport.TargetType.COUNTER


class _ModerationReportUpdateView(RoleRequiredMixin, View):
    required_roles = MODERATION_ROLES
    next_status = None
    audit_action = ""

    def post(self, request, *args, **kwargs):
        report = get_object_or_404(ContentReport, pk=kwargs.get("pk"))
        if report.status != ContentReport.Status.OPEN:
            return redirect("thinking:moderation_panel")
        report.status = self.next_status
        report.resolved_at = timezone.now()
        report.resolved_by = request.user
        report.save(update_fields=["status", "resolved_at", "resolved_by"])
        log_action(
            actor=request.user,
            action=self.audit_action,
            metadata={
                "report_id": report.id,
                "target_type": report.target_type,
                "target_id": report.target_id,
                "reason": report.reason,
            },
            request=request,
        )
        return redirect("thinking:moderation_panel")


class ReportResolveView(_ModerationReportUpdateView):
    next_status = ContentReport.Status.RESOLVED
    audit_action = "content.report_resolved"


class ReportDismissView(_ModerationReportUpdateView):
    next_status = ContentReport.Status.DISMISSED
    audit_action = "content.report_dismissed"


class ModerationReportBulkUpdateView(RoleRequiredMixin, View):
    required_roles = MODERATION_ROLES
    ACTION_TO_STATUS = {
        "resolve": (ContentReport.Status.RESOLVED, "content.report_resolved"),
        "dismiss": (ContentReport.Status.DISMISSED, "content.report_dismissed"),
    }

    def _redirect(self, request):
        query = request.POST.get("next_query", "").strip()
        url = reverse("thinking:moderation_panel")
        if query:
            return redirect(f"{url}?{query}")
        return redirect(url)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "").strip().lower()
        if action not in self.ACTION_TO_STATUS:
            return HttpResponse(
                "Invalid action",
                status=400,
                content_type="text/plain; charset=utf-8",
            )
        raw_ids = request.POST.getlist("report_ids")
        if not raw_ids:
            return HttpResponse(
                "No report_ids provided",
                status=400,
                content_type="text/plain; charset=utf-8",
            )
        try:
            report_ids = sorted({int(report_id) for report_id in raw_ids})
        except ValueError:
            return HttpResponse(
                "Invalid report_ids",
                status=400,
                content_type="text/plain; charset=utf-8",
            )

        next_status, audit_action = self.ACTION_TO_STATUS[action]
        with transaction.atomic():
            locked_reports = list(
                ContentReport.objects.select_for_update()
                .filter(id__in=report_ids)
                .order_by("id")
            )
            for report in locked_reports:
                if report.status != ContentReport.Status.OPEN:
                    continue
                report.status = next_status
                report.resolved_at = timezone.now()
                report.resolved_by = request.user
                report.save(update_fields=["status", "resolved_at", "resolved_by"])
                log_action(
                    actor=request.user,
                    action=audit_action,
                    metadata={
                        "report_id": report.id,
                        "target_type": report.target_type,
                        "target_id": report.target_id,
                        "reason": report.reason,
                    },
                    request=request,
                )
        return self._redirect(request)


class _ModerationStatusSetView(RoleRequiredMixin, View):
    required_roles = MODERATION_ROLES
    model = None

    def get_target(self, **kwargs):
        manager = getattr(self.model, "all_objects", self.model.objects)
        return get_object_or_404(manager, pk=kwargs.get("pk"))

    def redirect_response(self, target):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        target = self.get_target(**kwargs)
        old_status = target.status
        new_status = request.POST.get("status", "").strip().lower()
        allowed_next = STATUS_TRANSITIONS.get(old_status, set())
        if new_status not in allowed_next:
            return HttpResponse(
                "Invalid status transition",
                status=400,
                content_type="text/plain; charset=utf-8",
            )
        target.status = new_status
        target.save(update_fields=["status"])
        log_action(
            actor=request.user,
            action="moderation.status_change",
            target=target,
            metadata={"old_status": old_status, "new_status": new_status},
            request=request,
        )
        return self.redirect_response(target)


class ThesisStatusSetView(_ModerationStatusSetView):
    model = Thesis

    def redirect_response(self, target):
        return redirect("thinking:thesis_detail", pk=target.pk)


class CounterStatusSetView(_ModerationStatusSetView):
    model = Counter

    def redirect_response(self, target):
        return redirect("thinking:thesis_detail", pk=target.thesis_id)


class _ModerationDeleteRestoreView(RoleRequiredMixin, View):
    required_roles = MODERATION_ROLES
    model = None

    def get_target(self, **kwargs):
        manager = getattr(self.model, "all_objects", self.model.objects)
        return get_object_or_404(manager, pk=kwargs.get("pk"))

    def redirect_response(self, target):
        raise NotImplementedError

    def _metadata(self, target, previous_deleted_at):
        return {
            "model": target._meta.label_lower,
            "id": str(target.pk),
            "previous_deleted_at": (
                previous_deleted_at.isoformat() if previous_deleted_at else None
            ),
            "status": getattr(target, "status", ""),
            "reason": "",
        }


class ThesisSoftDeleteView(_ModerationDeleteRestoreView):
    model = Thesis

    def post(self, request, *args, **kwargs):
        thesis = self.get_target(**kwargs)
        previous_deleted_at = thesis.deleted_at
        if thesis.soft_delete(actor=request.user):
            metadata = self._metadata(thesis, previous_deleted_at)
            metadata["reason"] = request.POST.get("reason", "").strip()
            log_action(
                actor=request.user,
                action="moderation.soft_delete",
                target=thesis,
                metadata=metadata,
                request=request,
            )
        return redirect(
            f"{reverse('thinking:thesis_detail', kwargs={'pk': thesis.pk})}?include_deleted=1"
        )


class ThesisRestoreView(_ModerationDeleteRestoreView):
    model = Thesis

    def post(self, request, *args, **kwargs):
        thesis = self.get_target(**kwargs)
        previous_deleted_at = thesis.deleted_at
        if thesis.restore(actor=request.user):
            log_action(
                actor=request.user,
                action="moderation.restore",
                target=thesis,
                metadata=self._metadata(thesis, previous_deleted_at),
                request=request,
            )
        return redirect("thinking:thesis_detail", pk=thesis.pk)


class CounterSoftDeleteView(_ModerationDeleteRestoreView):
    model = Counter

    def post(self, request, *args, **kwargs):
        counter = self.get_target(**kwargs)
        previous_deleted_at = counter.deleted_at
        if counter.soft_delete(actor=request.user):
            metadata = self._metadata(counter, previous_deleted_at)
            metadata["reason"] = request.POST.get("reason", "").strip()
            log_action(
                actor=request.user,
                action="moderation.soft_delete",
                target=counter,
                metadata=metadata,
                request=request,
            )
        return redirect(
            f"{reverse('thinking:thesis_detail', kwargs={'pk': counter.thesis_id})}?include_deleted=1"
        )


class CounterRestoreView(_ModerationDeleteRestoreView):
    model = Counter

    def post(self, request, *args, **kwargs):
        counter = self.get_target(**kwargs)
        previous_deleted_at = counter.deleted_at
        if counter.restore(actor=request.user):
            log_action(
                actor=request.user,
                action="moderation.restore",
                target=counter,
                metadata=self._metadata(counter, previous_deleted_at),
                request=request,
            )
        return redirect("thinking:thesis_detail", pk=counter.thesis_id)

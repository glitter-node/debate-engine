"""
app.thinking.views - Views for the "thinking" app.
"""

import csv
from datetime import timedelta
from io import StringIO

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView
from thinking.audit import log_action
from thinking.content_status import CONTENT_STATUS_CHOICES, ContentStatus
from thinking.domain.argument_service import (
    add_evidence_to_claim,
    cast_vote_for_claim,
    calculate_thesis_claim_scores,
    create_claim_for_thesis,
    create_claim_from_argument,
    create_claim_from_counter,
    create_counter_for_thesis,
    create_thesis_with_arguments,
    merge_claims,
    rebuild_thesis_inference_safe,
    review_duplicate_pair,
    update_claim_with_revision,
)
from thinking.forms import (
    ArgumentFormSet,
    ClaimDuplicateReviewForm,
    ClaimEditForm,
    ClaimEvidenceForm,
    ClaimForm,
    ClaimMergeSelectionForm,
    CounterForm,
    ThesisForm,
)
from thinking.models import (
    Argument,
    Claim,
    ClaimEntity,
    ClaimVote,
    ContentReport,
    Counter,
    Thesis,
)
from thinking.moderation.moderation_actions import (
    apply_report_status,
    bulk_apply_report_status,
)
from thinking.moderation.report_service import submit_content_report
from thinking.moderation_metrics import (
    ALLOWED_SINCE_DAYS,
    DEFAULT_SINCE_DAYS,
    ESCALATION_LEVEL_1,
    build_moderation_metrics,
    escalation_min_count_for_level,
    get_stale_open_hours,
)
from thinking.queries.argument_queries import counters_by_argument, flatten_counters
from thinking.queries.claim_graph import (
    build_claim_graph,
    build_legacy_claim_records,
    claims_by_entity,
    claim_contradiction_map,
    claim_merge_candidates,
    claim_merge_history,
    claim_normalized_map,
    claim_score_map,
    claim_inference_map,
    claim_support_closure_map,
    claim_triples_for_thesis,
    contradictions_for_thesis,
    duplicate_claim_candidates,
    duplicate_claim_reviews,
    duplicate_claim_suggestions,
    debate_claim_mapping_maps,
    claim_evidence_map,
    ranked_claims_for_thesis,
    claim_revision_map,
    claim_user_votes,
    claim_vote_totals,
)
from thinking.queries.moderation_queries import build_report_rows
from thinking.queries.thesis_tree import thesis_detail_queryset
from thinking.report_rate_limit import allow_report_submit
from thinking.roles import RoleRequiredMixin, role_required, user_has_site_role
from thinking.site_roles import SiteRole

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


def _build_report_rows(reports, stale_cutoff=None):
    return build_report_rows(reports, stale_cutoff=stale_cutoff)


def _claim_merge_preview_context(*, form, search_query: str):
    source_claim = None
    target_claim = None
    if form.is_bound and form.is_valid():
        source_claim = form.cleaned_data["source_claim"]
        target_claim = form.cleaned_data["target_claim"]
    source_stats = {
        "evidence_count": source_claim.evidence_items.count() if source_claim else 0,
        "vote_count": source_claim.votes.count() if source_claim else 0,
        "outgoing_count": (
            source_claim.outgoing_relations.count() if source_claim else 0
        ),
        "incoming_count": (
            source_claim.incoming_relations.count() if source_claim else 0
        ),
    }
    target_stats = {
        "evidence_count": target_claim.evidence_items.count() if target_claim else 0,
        "vote_count": target_claim.votes.count() if target_claim else 0,
        "outgoing_count": (
            target_claim.outgoing_relations.count() if target_claim else 0
        ),
        "incoming_count": (
            target_claim.incoming_relations.count() if target_claim else 0
        ),
    }
    return {
        "candidate_claims": claim_merge_candidates(search_query=search_query)[:50],
        "form": form,
        "search_query": search_query,
        "source_claim": source_claim,
        "source_stats": source_stats,
        "target_claim": target_claim,
        "target_stats": target_stats,
        "source_history": claim_merge_history(claim=source_claim)[:20]
        if source_claim
        else [],
        "target_history": claim_merge_history(claim=target_claim)[:20]
        if target_claim
        else [],
    }


def _claim_duplicate_review_context(*, form, search_query: str):
    claim_a = None
    claim_b = None
    if form.is_bound and form.is_valid():
        claim_a = form.cleaned_data["claim_a"]
        claim_b = form.cleaned_data["claim_b"]
    return {
        "candidate_duplicates": duplicate_claim_candidates()[:50],
        "duplicate_reviews": duplicate_claim_reviews()[:20],
        "form": form,
        "search_query": search_query,
        "claim_a": claim_a,
        "claim_b": claim_b,
    }


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
        return thesis_detail_queryset(
            can_moderate=can_moderate,
            include_deleted=include_deleted,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        thesis = ctx["thesis"]
        arguments = list(thesis.arguments.all())
        claims = list(thesis.claims.all())
        if claims and not thesis.claims.filter(
            Q(inference_sources_a__isnull=False)
            | Q(inference_sources_b__isnull=False)
            | Q(contradictions_left__isnull=False)
            | Q(contradictions_right__isnull=False)
            | Q(support_closure_sources__isnull=False)
        ).exists():
            rebuild_thesis_inference_safe(thesis=thesis)
        if claims and any(getattr(claim, "score", None) is None for claim in claims):
            score_instances = calculate_thesis_claim_scores(thesis)
            for claim in claims:
                claim.score = score_instances.get(claim.id)
        all_counters = list(thesis.counters.all())
        claim_mappings = list(thesis.claim_mappings.all())
        counters_by_argument_map = counters_by_argument(arguments, all_counters)
        all_visible_counters = flatten_counters(
            [c for counters in counters_by_argument_map.values() for c in counters]
        )
        argument_claim_map, counter_claim_map = debate_claim_mapping_maps(
            claim_mappings
        )
        claim_roots = build_claim_graph(claims)
        ctx["arguments"] = arguments
        ctx["argument_claim_map"] = argument_claim_map
        ctx["claim_roots"] = claim_roots
        ctx["claim_count"] = len(claims)
        ctx["claim_score_map"] = claim_score_map(claims)
        (
            ctx["claim_normalized_map"],
            ctx["claim_alias_map"],
        ) = claim_normalized_map(claims)
        ctx["claim_vote_totals"] = claim_vote_totals(claims)
        ctx["claim_evidence_map"] = claim_evidence_map(claims)
        ranked_queryset = ranked_claims_for_thesis(thesis=thesis)
        ranked_claim_ids = list(ranked_queryset.values_list("claim_id", flat=True))
        ctx["ranked_claims"] = list(ranked_queryset[:10])
        ctx["claim_rank_positions"] = {
            claim_id: position
            for position, claim_id in enumerate(ranked_claim_ids, start=1)
        }
        ctx["claim_revision_map"] = claim_revision_map(claims)
        ctx["claim_triples"] = list(claim_triples_for_thesis(thesis=thesis)[:20])
        ctx["claim_inference_map"] = claim_inference_map(thesis=thesis)
        ctx["claim_contradiction_map"] = claim_contradiction_map(thesis=thesis)
        ctx["claim_support_closure_map"] = claim_support_closure_map(thesis=thesis)
        ctx["thesis_contradictions"] = list(
            contradictions_for_thesis(thesis=thesis)[:10]
        )
        selected_entity = self.request.GET.get("entity", "").strip().lower()
        ctx["selected_entity"] = selected_entity
        selected_entity_obj = None
        if selected_entity:
            selected_entity_obj = ClaimEntity.objects.filter(
                canonical_name=selected_entity
            ).first()
        ctx["selected_entity_claims"] = (
            list(claims_by_entity(entity=selected_entity_obj))
            if selected_entity_obj is not None
            else []
        )
        ctx["legacy_claim_records"] = build_legacy_claim_records(
            thesis=thesis,
            arguments=arguments,
            counters_by_argument_map=counters_by_argument_map,
        )
        ctx["counters_by_argument"] = counters_by_argument_map
        ctx["counter_claim_map"] = counter_claim_map
        ctx["can_moderate"] = user_has_site_role(self.request.user, *MODERATION_ROLES)
        ctx["include_deleted"] = _can_include_deleted(self.request)
        ctx["status_choices"] = CONTENT_STATUS_CHOICES
        ctx["thesis_next_statuses"] = _allowed_next_statuses(thesis.status)
        ctx["counter_next_statuses"] = {
            c.id: _allowed_next_statuses(c.status) for c in all_visible_counters
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
                    counter_id__in=[c.id for c in all_visible_counters],
                ).values_list("counter_id", flat=True)
            )
            user_claim_votes = claim_user_votes(
                ClaimVote.objects.filter(
                    user=self.request.user,
                    claim_id__in=[claim.id for claim in claims],
                )
            )
        else:
            thesis_open = False
            counter_open_ids = set()
            user_claim_votes = {}
        ctx["report_reason_choices"] = REPORT_REASONS
        ctx["thesis_report_open"] = thesis_open
        ctx["counter_report_open_ids"] = counter_open_ids
        ctx["user_claim_votes"] = user_claim_votes
        return ctx


class ThesisCreateView(LoginRequiredMixin, FormView):
    template_name = "thinking/thesis_create.html"
    form_class = ThesisForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "argument_formset" not in ctx:
            ctx["argument_formset"] = ArgumentFormSet()
        return ctx

    def post(self, request, *_args, **_kwargs):
        form = self.get_form()
        argument_formset = ArgumentFormSet(request.POST)
        if form.is_valid() and argument_formset.is_valid():
            thesis = create_thesis_with_arguments(
                form=form,
                argument_formset=argument_formset,
                author=request.user,
            )
            if thesis is None:
                return self.form_invalid(form)
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
        raw_parent_id = request.POST.get("parent_counter") or request.GET.get(
            "parent_counter"
        )
        self.parent_counter = None
        if raw_parent_id:
            try:
                parent_id = int(raw_parent_id)
            except (TypeError, ValueError):
                raise Http404 from None
            self.parent_counter = Counter.objects.filter(
                pk=parent_id,
                thesis=self.thesis,
            ).first()
            if self.parent_counter is None:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        target_queryset = self.thesis.arguments.all()
        if self.parent_counter is not None:
            target_queryset = target_queryset.filter(
                pk=self.parent_counter.target_argument_id
            )
            form.initial["target_argument"] = self.parent_counter.target_argument_id
            form.initial["parent_counter"] = self.parent_counter.id
        form.fields["target_argument"].queryset = target_queryset
        form.fields["parent_counter"].queryset = (
            Counter.objects.filter(pk=self.parent_counter.pk)
            if self.parent_counter
            else Counter.objects.none()
        )
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["thesis"] = self.thesis
        ctx["parent_counter"] = self.parent_counter
        return ctx

    def form_valid(self, form):
        create_counter_for_thesis(
            form=form,
            thesis=self.thesis,
            author=self.request.user,
            parent_counter=self.parent_counter,
        )
        cache.delete("thinking:home:lists")
        return redirect("thinking:thesis_detail", pk=self.thesis.pk)


class ClaimCreateView(LoginRequiredMixin, FormView):
    template_name = "thinking/claim_create.html"
    form_class = ClaimForm

    def dispatch(self, request, *args, **kwargs):
        self.thesis = Thesis.objects.filter(pk=kwargs.get("pk")).first()
        if not self.thesis:
            raise Http404
        self.target_claim = None
        raw_target_id = request.POST.get("target_claim") or request.GET.get(
            "target_claim"
        )
        if raw_target_id:
            try:
                target_id = int(raw_target_id)
            except (TypeError, ValueError):
                raise Http404 from None
            self.target_claim = Claim.objects.filter(
                pk=target_id,
                thesis=self.thesis,
            ).first()
            if self.target_claim is None:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["thesis"] = self.thesis
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.target_claim is not None:
            form.initial["target_claim"] = self.target_claim.id
        raw_relation_type = self.request.GET.get("relation_type")
        if raw_relation_type and not self.request.POST:
            relation_type = form.fields["relation_type"].queryset.filter(
                code=raw_relation_type
            ).first()
            if relation_type is not None:
                form.initial["relation_type"] = relation_type.id
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["thesis"] = self.thesis
        ctx["target_claim"] = self.target_claim
        form = ctx.get("form") or self.get_form()
        claim_body = ""
        if self.request.method == "POST":
            claim_body = self.request.POST.get("body", "").strip()
        elif form.initial.get("body"):
            claim_body = form.initial["body"]
        ctx["duplicate_suggestions"] = (
            duplicate_claim_suggestions(
                thesis=self.thesis,
                body=claim_body,
            )
            if claim_body
            else []
        )
        return ctx

    def form_valid(self, form):
        create_claim_for_thesis(
            form=form,
            thesis=self.thesis,
            author=self.request.user,
        )
        cache.delete("thinking:home:lists")
        return redirect("thinking:thesis_detail", pk=self.thesis.pk)


class ClaimEvidenceCreateView(LoginRequiredMixin, FormView):
    template_name = "thinking/claim_evidence_create.html"
    form_class = ClaimEvidenceForm

    def dispatch(self, request, *args, **kwargs):
        self.claim = get_object_or_404(Claim, pk=kwargs.get("pk"))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["claim"] = self.claim
        ctx["thesis"] = self.claim.thesis
        return ctx

    def form_valid(self, form):
        add_evidence_to_claim(
            form=form,
            claim=self.claim,
            created_by=self.request.user,
        )
        return redirect("thinking:thesis_detail", pk=self.claim.thesis_id)


class ClaimEditView(LoginRequiredMixin, FormView):
    template_name = "thinking/claim_edit.html"
    form_class = ClaimEditForm

    def dispatch(self, request, *args, **kwargs):
        self.claim = get_object_or_404(Claim, pk=kwargs.get("pk"))
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.claim
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["claim"] = self.claim
        ctx["thesis"] = self.claim.thesis
        return ctx

    def form_valid(self, form):
        update_claim_with_revision(
            form=form,
            claim=self.claim,
            edited_by=self.request.user,
        )
        return redirect("thinking:thesis_detail", pk=self.claim.thesis_id)


class ClaimVoteCreateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        claim = get_object_or_404(Claim, pk=kwargs.get("pk"))
        vote_type = request.POST.get("vote_type", "").strip().lower()
        if vote_type not in {
            ClaimVote.VoteType.UPVOTE,
            ClaimVote.VoteType.DOWNVOTE,
        }:
            raise Http404
        try:
            cast_vote_for_claim(
                claim=claim,
                user=request.user,
                vote_type=vote_type,
            )
        except ValidationError:
            pass
        return redirect("thinking:thesis_detail", pk=claim.thesis_id)


class _StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return bool(user and user.is_active and (user.is_staff or user.is_superuser))


class ClaimMergePreviewView(_StaffRequiredMixin, FormView):
    template_name = "thinking/claim_merge_preview.html"
    form_class = ClaimMergeSelectionForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["search_query"] = self.request.GET.get("q", "").strip()
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        ctx.update(
            _claim_merge_preview_context(
                form=form,
                search_query=self.request.GET.get("q", "").strip(),
            )
        )
        return ctx

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        return self.render_to_response(self.get_context_data(form=form))


class ClaimMergeView(_StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        search_query = request.GET.get("q", "").strip()
        form = ClaimMergeSelectionForm(request.POST, search_query=search_query)
        if not form.is_valid():
            return render(
                request,
                "thinking/claim_merge_preview.html",
                _claim_merge_preview_context(form=form, search_query=search_query),
            )
        try:
            merge_claims(
                source_claim=form.cleaned_data["source_claim"],
                target_claim=form.cleaned_data["target_claim"],
                admin_user=request.user,
                reason=form.cleaned_data.get("reason", ""),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                "thinking/claim_merge_preview.html",
                _claim_merge_preview_context(form=form, search_query=search_query),
            )
        return redirect("thinking:claim_merge_preview")


class ClaimDuplicateReviewPreviewView(_StaffRequiredMixin, FormView):
    template_name = "thinking/claim_duplicate_review.html"
    form_class = ClaimDuplicateReviewForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["search_query"] = self.request.GET.get("q", "").strip()
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            _claim_duplicate_review_context(
                form=ctx["form"],
                search_query=self.request.GET.get("q", "").strip(),
            )
        )
        return ctx

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        return self.render_to_response(self.get_context_data(form=form))


class ClaimDuplicateReviewView(_StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        search_query = request.GET.get("q", "").strip()
        form = ClaimDuplicateReviewForm(request.POST, search_query=search_query)
        if not form.is_valid():
            return render(
                request,
                "thinking/claim_duplicate_review.html",
                _claim_duplicate_review_context(form=form, search_query=search_query),
            )
        try:
            review_duplicate_pair(
                claim_a=form.cleaned_data["claim_a"],
                claim_b=form.cleaned_data["claim_b"],
                decision=form.cleaned_data["decision"],
                reviewed_by=request.user,
                reason=form.cleaned_data.get("reason", ""),
                merge_func=merge_claims,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                "thinking/claim_duplicate_review.html",
                _claim_duplicate_review_context(form=form, search_query=search_query),
            )
        return redirect("thinking:claim_duplicate_review_preview")


class ArgumentClaimConvertView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        argument = get_object_or_404(Argument, pk=kwargs.get("pk"))
        try:
            create_claim_from_argument(argument=argument, author=request.user)
        except ValidationError:
            pass
        return redirect("thinking:thesis_detail", pk=argument.thesis_id)


class CounterClaimConvertView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        counter = get_object_or_404(Counter, pk=kwargs.get("pk"))
        try:
            create_claim_from_counter(counter=counter, author=request.user)
        except ValidationError:
            pass
        return redirect("thinking:thesis_detail", pk=counter.thesis_id)


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

    def post(self, request, *_args, **kwargs):
        target = self.get_target(**kwargs)
        return_url = _report_return_url_for(target)
        if not allow_report_submit(getattr(request.user, "id", None)):
            return redirect(return_url)

        result = submit_content_report(
            request=request,
            target=target,
            target_type=self.target_type,
            allowed_reasons=REPORT_REASONS,
        )
        if result["created"]:
            log_action(
                actor=request.user,
                action="content.report_submitted",
                target=target,
                metadata={
                    "target_type": self.target_type,
                    "target_id": str(target.pk),
                    "reason": result["reason"],
                },
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

    def post(self, request, *_args, **kwargs):
        report = get_object_or_404(ContentReport, pk=kwargs.get("pk"))
        if not apply_report_status(
            report=report,
            next_status=self.next_status,
            actor=request.user,
        ):
            return redirect("thinking:moderation_panel")
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

    def post(self, request, *_args, **kwargs):
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
        for report in bulk_apply_report_status(
            report_ids=report_ids,
            next_status=next_status,
            actor=request.user,
        ):
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

    def post(self, request, *_args, **kwargs):
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

    def post(self, request, *_args, **kwargs):
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

    def post(self, request, *_args, **kwargs):
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

    def post(self, request, *_args, **kwargs):
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

    def post(self, request, *_args, **kwargs):
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

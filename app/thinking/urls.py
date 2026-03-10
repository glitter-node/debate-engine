"""
app.thinking.urls - URL configuration for the "thinking" app.
"""

from django.urls import path

from .views import (
    ArgumentClaimConvertView,
    ClaimCreateView,
    ClaimDuplicateReviewPreviewView,
    ClaimDuplicateReviewView,
    ClaimEditView,
    ClaimEvidenceCreateView,
    ClaimMergePreviewView,
    ClaimMergeView,
    ClaimVoteCreateView,
    CounterClaimConvertView,
    CounterReportCreateView,
    CounterRestoreView,
    CounterSoftDeleteView,
    CounterStatusSetView,
    CounterCreateView,
    HomeView,
    ModerationReportBulkUpdateView,
    ReportDismissView,
    ReportResolveView,
    ThesisReportCreateView,
    moderation_mark_reviewed,
    moderation_metrics_csv,
    moderation_panel,
    ThesisRestoreView,
    ThesisSoftDeleteView,
    ThesisStatusSetView,
    ThesisCreateView,
    ThesisDetailView,
    ThesisListView,
)

app_name = "thinking"

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("theses/", ThesisListView.as_view(), name="thesis_list"),
    path("theses/new/", ThesisCreateView.as_view(), name="thesis_create"),
    path("theses/<int:pk>/", ThesisDetailView.as_view(), name="thesis_detail"),
    path("theses/<int:pk>/claims/new/", ClaimCreateView.as_view(), name="claim_create"),
    path("claims/<int:pk>/edit/", ClaimEditView.as_view(), name="claim_edit"),
    path(
        "claims/<int:pk>/evidence/new/",
        ClaimEvidenceCreateView.as_view(),
        name="claim_evidence_create",
    ),
    path(
        "claims/<int:pk>/vote/",
        ClaimVoteCreateView.as_view(),
        name="claim_vote",
    ),
    path(
        "claims/merge/",
        ClaimMergePreviewView.as_view(),
        name="claim_merge_preview",
    ),
    path(
        "claims/duplicates/",
        ClaimDuplicateReviewPreviewView.as_view(),
        name="claim_duplicate_review_preview",
    ),
    path(
        "claims/duplicates/review/",
        ClaimDuplicateReviewView.as_view(),
        name="claim_duplicate_review",
    ),
    path(
        "claims/merge/execute/",
        ClaimMergeView.as_view(),
        name="claim_merge",
    ),
    path(
        "arguments/<int:pk>/archive/",
        ArgumentClaimConvertView.as_view(),
        name="argument_claim_convert",
    ),
    path(
        "counters/<int:pk>/archive/",
        CounterClaimConvertView.as_view(),
        name="counter_claim_convert",
    ),
    path(
        "theses/<int:pk>/counter/", CounterCreateView.as_view(), name="counter_create"
    ),
    path(
        "report/thesis/<int:pk>/",
        ThesisReportCreateView.as_view(),
        name="report_thesis",
    ),
    path(
        "report/counter/<int:pk>/",
        CounterReportCreateView.as_view(),
        name="report_counter",
    ),
    path(
        "moderation/thesis/<int:pk>/set-status/",
        ThesisStatusSetView.as_view(),
        name="moderation_thesis_set_status",
    ),
    path(
        "moderation/counter/<int:pk>/set-status/",
        CounterStatusSetView.as_view(),
        name="moderation_counter_set_status",
    ),
    path(
        "moderation/thesis/<int:pk>/delete/",
        ThesisSoftDeleteView.as_view(),
        name="moderation_thesis_delete",
    ),
    path(
        "moderation/thesis/<int:pk>/restore/",
        ThesisRestoreView.as_view(),
        name="moderation_thesis_restore",
    ),
    path(
        "moderation/counter/<int:pk>/delete/",
        CounterSoftDeleteView.as_view(),
        name="moderation_counter_delete",
    ),
    path(
        "moderation/counter/<int:pk>/restore/",
        CounterRestoreView.as_view(),
        name="moderation_counter_restore",
    ),
    path("moderation/", moderation_panel, name="moderation_panel"),
    path(
        "moderation/metrics.csv", moderation_metrics_csv, name="moderation_metrics_csv"
    ),
    path(
        "moderation/mark-reviewed/",
        moderation_mark_reviewed,
        name="moderation_mark_reviewed",
    ),
    path(
        "moderation/reports/bulk/",
        ModerationReportBulkUpdateView.as_view(),
        name="moderation_reports_bulk",
    ),
    path(
        "moderation/report/<int:pk>/resolve/",
        ReportResolveView.as_view(),
        name="moderation_report_resolve",
    ),
    path(
        "moderation/report/<int:pk>/dismiss/",
        ReportDismissView.as_view(),
        name="moderation_report_dismiss",
    ),
]

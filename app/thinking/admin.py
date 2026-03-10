# pylint: disable=no-member
"""
app.thinking.admin - Admin configuration for the "thinking" app.
"""

import json
from typing import cast

from django import forms
from django.contrib import admin
from django.forms import ModelChoiceField
from django.utils.html import escape, format_html

from .models import (
    Argument,
    AuditLog,
    Claim,
    ClaimAlias,
    ClaimCanonical,
    ClaimContradiction,
    ClaimDuplicateReview,
    ClaimEmbedding,
    ClaimEntity,
    ClaimEvidence,
    ClaimInference,
    ClaimInferenceRule,
    ClaimMergeLog,
    ClaimNormalized,
    ClaimPredicate,
    ClaimRelation,
    ClaimRelationType,
    ClaimSimilarity,
    ClaimSupportClosure,
    ClaimTriple,
    ClaimRevision,
    ClaimScore,
    ClaimVote,
    DebateClaimMapping,
    Counter,
    Thesis,
)


class ArgumentInline(admin.TabularInline):
    model = Argument
    extra = 0


@admin.register(Argument)
class ArgumentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "thesis",
        "order",
        "short_body",
        "created_at",
    )
    list_filter = ("thesis", "created_at")
    search_fields = ("thesis__title", "body")
    ordering = ("thesis__id", "order", "id")
    list_select_related = ("thesis",)

    @admin.display(description="Body")
    def short_body(self, obj):
        text = " ".join((obj.body or "").split())
        if len(text) <= 80:
            return text
        return f"{text[:77]}..."


class CounterAdminForm(forms.ModelForm):
    class Meta:
        model = Counter
        fields = "__all__"


@admin.register(Thesis)
class ThesisAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "stance", "author", "created_at", "updated_at")
    search_fields = ("title", "summary", "author__username")
    list_filter = ("stance", "created_at")
    inlines = [ArgumentInline]


@admin.register(Counter)
class CounterAdmin(admin.ModelAdmin):
    form = CounterAdminForm
    list_display = ("id", "thesis", "target_argument", "author", "created_at")
    search_fields = ("body", "author__username")
    list_select_related = ("thesis", "target_argument", "author")

    def _target_argument_label(self, obj):
        thesis_title = "Missing thesis"
        if obj.thesis_id:
            try:
                thesis_title = obj.thesis.title
            except Thesis.DoesNotExist:
                thesis_title = f"Missing thesis #{obj.thesis_id}"
        return f"{thesis_title} · A{obj.order}"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "target_argument" and field is not None:
            model_field = cast(ModelChoiceField, field)
            model_field.label_from_instance = self._target_argument_label
        return field


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "thesis", "author", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("body", "thesis__title", "author__username")
    list_select_related = ("thesis", "author")


@admin.register(ClaimRelation)
class ClaimRelationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_claim",
        "relation_type",
        "target_claim",
        "created_at",
    )
    list_filter = ("relation_type", "created_at")
    list_select_related = (
        "source_claim",
        "target_claim",
        "relation_type",
    )


@admin.register(ClaimRelationType)
class ClaimRelationTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "label")
    search_fields = ("code", "label")


@admin.register(ClaimEvidence)
class ClaimEvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "claim",
        "title",
        "source_label",
        "citation_count",
        "trust_score",
        "created_by",
        "created_at",
    )
    search_fields = ("title", "url", "excerpt", "source_label")
    list_select_related = ("claim", "created_by")


@admin.register(ClaimEmbedding)
class ClaimEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("claim", "embedding_model", "created_at")
    search_fields = ("claim__body", "embedding_model")
    list_select_related = ("claim",)


@admin.register(ClaimEntity)
class ClaimEntityAdmin(admin.ModelAdmin):
    list_display = ("canonical_name", "entity_type", "created_at")
    search_fields = ("name", "canonical_name")


@admin.register(ClaimPredicate)
class ClaimPredicateAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name", "description")


@admin.register(ClaimTriple)
class ClaimTripleAdmin(admin.ModelAdmin):
    list_display = (
        "claim",
        "subject_entity",
        "predicate",
        "object_entity",
        "confidence",
    )
    list_select_related = ("claim", "subject_entity", "predicate", "object_entity")


@admin.register(ClaimNormalized)
class ClaimNormalizedAdmin(admin.ModelAdmin):
    list_display = ("claim", "triple", "normalization_method", "confidence")
    list_select_related = ("claim", "triple")


@admin.register(ClaimAlias)
class ClaimAliasAdmin(admin.ModelAdmin):
    list_display = ("claim", "alias_text", "created_at")
    search_fields = ("alias_text", "claim__body")
    list_select_related = ("claim",)


@admin.register(ClaimSimilarity)
class ClaimSimilarityAdmin(admin.ModelAdmin):
    list_display = ("claim_a", "claim_b", "similarity_score", "detected_at")
    list_select_related = ("claim_a", "claim_b")
    ordering = ("-similarity_score", "-detected_at")


@admin.register(ClaimInferenceRule)
class ClaimInferenceRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "pattern_predicate_a",
        "pattern_predicate_b",
        "inferred_predicate",
        "confidence_weight",
    )
    search_fields = (
        "name",
        "pattern_predicate_a",
        "pattern_predicate_b",
        "inferred_predicate",
    )


@admin.register(ClaimInference)
class ClaimInferenceAdmin(admin.ModelAdmin):
    list_display = (
        "source_claim_a",
        "source_claim_b",
        "inferred_claim",
        "rule",
        "confidence",
        "created_at",
    )
    list_select_related = (
        "source_claim_a",
        "source_claim_b",
        "inferred_claim",
        "rule",
    )
    ordering = ("-confidence", "-created_at")


@admin.register(ClaimContradiction)
class ClaimContradictionAdmin(admin.ModelAdmin):
    list_display = ("claim_a", "claim_b", "contradiction_type", "confidence")
    list_select_related = ("claim_a", "claim_b")
    ordering = ("-confidence", "claim_a_id", "claim_b_id")


@admin.register(ClaimSupportClosure)
class ClaimSupportClosureAdmin(admin.ModelAdmin):
    list_display = ("source_claim", "target_claim", "support_depth", "confidence")
    list_select_related = ("source_claim", "target_claim")
    ordering = ("support_depth", "-confidence")


@admin.register(ClaimDuplicateReview)
class ClaimDuplicateReviewAdmin(admin.ModelAdmin):
    list_display = ("claim_a", "claim_b", "decision", "reviewed_by", "reviewed_at")
    list_select_related = ("claim_a", "claim_b", "reviewed_by")
    ordering = ("-reviewed_at",)


@admin.register(ClaimVote)
class ClaimVoteAdmin(admin.ModelAdmin):
    list_display = ("id", "claim", "user", "vote_type", "created_at")
    list_filter = ("vote_type", "created_at")
    list_select_related = ("claim", "user")


@admin.register(ClaimRevision)
class ClaimRevisionAdmin(admin.ModelAdmin):
    list_display = ("id", "claim", "edited_by", "edited_at")
    search_fields = ("previous_body",)
    list_select_related = ("claim", "edited_by")


@admin.register(ClaimScore)
class ClaimScoreAdmin(admin.ModelAdmin):
    list_display = (
        "claim",
        "final_score",
        "bayesian_vote_score",
        "vote_score",
        "evidence_score",
        "support_score",
        "oppose_score",
        "pagerank_score",
        "calculated_at",
    )
    list_select_related = ("claim",)
    ordering = ("-final_score", "claim_id")


@admin.register(ClaimCanonical)
class ClaimCanonicalAdmin(admin.ModelAdmin):
    list_display = ("id", "claim", "canonical_claim", "created_at")
    list_select_related = ("claim", "canonical_claim")


@admin.register(ClaimMergeLog)
class ClaimMergeLogAdmin(admin.ModelAdmin):
    list_display = ("id", "source_claim", "target_claim", "merged_by", "merged_at")
    search_fields = ("reason",)
    list_select_related = ("source_claim", "target_claim", "merged_by")


@admin.register(DebateClaimMapping)
class DebateClaimMappingAdmin(admin.ModelAdmin):
    list_display = ("id", "thesis", "argument", "counter", "claim", "created_at")
    list_select_related = ("thesis", "argument", "counter", "claim")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "actor",
        "actor_role",
        "action",
        "target_model",
        "target_id",
        "ip_address",
    )
    list_filter = ("action", "actor_role", "created_at")
    search_fields = (
        "actor__username",
        "action",
        "target_model",
        "target_id",
        "ip_address",
    )
    ordering = ("-created_at",)
    list_per_page = 50
    date_hierarchy = "created_at"
    list_select_related = ("actor",)
    readonly_fields = (
        "id",
        "created_at",
        "actor",
        "actor_role",
        "action",
        "target_model",
        "target_id",
        "metadata_pretty",
        "ip_address",
        "user_agent",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        user = request.user
        return bool(user and user.is_active and user.is_staff)

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj):
        pretty = json.dumps(
            obj.metadata or {}, indent=2, sort_keys=True, ensure_ascii=False
        )
        return format_html("<pre>{}</pre>", escape(pretty))

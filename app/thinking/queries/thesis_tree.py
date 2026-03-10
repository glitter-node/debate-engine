from django.db.models import Count, Prefetch, Q

from ..content_status import ContentStatus
from ..models import (
    Argument,
    ClaimAlias,
    Claim,
    ClaimEvidence,
    ClaimNormalized,
    ClaimRelation,
    ClaimRevision,
    ClaimVote,
    DebateClaimMapping,
    Counter,
    Thesis,
)


def thesis_detail_queryset(*, can_moderate: bool, include_deleted: bool):
    counter_manager = (
        Counter.all_objects if (can_moderate and include_deleted) else Counter.objects
    )
    counters_qs = counter_manager.select_related(
        "author", "target_argument", "parent_counter"
    )
    if not can_moderate:
        counters_qs = counters_qs.filter(status=ContentStatus.ACTIVE)
    claims_qs = (
        Claim.objects.select_related("author", "score")
        .annotate(
            upvote_count=Count(
                "votes",
                filter=Q(votes__vote_type=ClaimVote.VoteType.UPVOTE),
            ),
            downvote_count=Count(
                "votes",
                filter=Q(votes__vote_type=ClaimVote.VoteType.DOWNVOTE),
            ),
        )
        .prefetch_related(
            Prefetch(
                "outgoing_relations",
                queryset=ClaimRelation.objects.select_related(
                    "relation_type",
                    "target_claim",
                    "target_claim__author",
                ),
            ),
            Prefetch(
                "evidence_items",
                queryset=ClaimEvidence.objects.select_related("created_by"),
            ),
            Prefetch(
                "revisions",
                queryset=ClaimRevision.objects.select_related("edited_by"),
            ),
            Prefetch(
                "aliases",
                queryset=ClaimAlias.objects.order_by("alias_text"),
            ),
            Prefetch(
                "normalized",
                queryset=ClaimNormalized.objects.select_related(
                    "triple",
                    "triple__subject_entity",
                    "triple__predicate",
                    "triple__object_entity",
                ),
            ),
        )
    )
    if not can_moderate:
        claims_qs = claims_qs.filter(status=ContentStatus.ACTIVE)
    arguments_qs = Argument.objects.all()
    thesis_manager = (
        Thesis.all_objects if (can_moderate and include_deleted) else Thesis.objects
    )
    qs = thesis_manager.select_related("author").prefetch_related(
        Prefetch("arguments", queryset=arguments_qs),
        Prefetch("claims", queryset=claims_qs),
        Prefetch("counters", queryset=counters_qs),
        Prefetch(
            "claim_mappings",
            queryset=DebateClaimMapping.objects.select_related(
                "claim",
                "argument",
                "counter",
            ),
        ),
    )
    if not can_moderate:
        qs = qs.filter(status=ContentStatus.ACTIVE)
    return qs

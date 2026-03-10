import math
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q

from ..content_status import ContentStatus
from ..domain.chain_validator import validate_claim_merge, validate_claim_merge_graph
from ..models import (
    Claim,
    ClaimCanonical,
    ClaimEvidence,
    ClaimMergeLog,
    ClaimRelation,
    ClaimRelationType,
    ClaimRevision,
    ClaimScore,
    ClaimVote,
    DebateClaimMapping,
    Thesis,
)
from .claim_duplicates import refresh_claim_similarity
from .claim_inference import rebuild_thesis_inference_safe
from ..scoring import (
    CLAIM_BAYESIAN_PRIOR,
    CLAIM_EVIDENCE_DEFAULT_TRUST,
    CLAIM_EVIDENCE_DOMAIN_WEIGHTS,
    CLAIM_EVIDENCE_MAX_TRUST,
    CLAIM_EVIDENCE_SOURCE_WEIGHTS,
    CLAIM_OPPOSE_RELATION_WEIGHTS,
    CLAIM_PAGERANK_DAMPING,
    CLAIM_PAGERANK_MAX_ITERATIONS,
    CLAIM_PAGERANK_TOLERANCE,
    CLAIM_SUPPORT_RELATION_WEIGHTS,
)


def _claim_score_queryset(*, thesis):
    return (
        Claim.objects.filter(thesis=thesis, status=ContentStatus.ACTIVE)
        .exclude(canonical_record__isnull=False)
        .select_related("thesis", "author", "score")
        .annotate(
            upvote_count=Count(
                "votes",
                filter=Q(votes__vote_type=ClaimVote.VoteType.UPVOTE),
                distinct=True,
            ),
            downvote_count=Count(
                "votes",
                filter=Q(votes__vote_type=ClaimVote.VoteType.DOWNVOTE),
                distinct=True,
            ),
        )
        .prefetch_related(
            Prefetch(
                "outgoing_relations",
                queryset=ClaimRelation.objects.select_related(
                    "relation_type",
                    "target_claim",
                ).order_by("created_at", "id"),
            ),
            Prefetch("evidence_items", queryset=ClaimEvidence.objects.order_by("id")),
        )
    )


def compute_bayesian_vote_score(*, upvotes, downvotes):
    total_votes = upvotes + downvotes
    return (upvotes + CLAIM_BAYESIAN_PRIOR) / (
        total_votes + (2.0 * CLAIM_BAYESIAN_PRIOR)
    )


def _evidence_source_weight(evidence):
    source_label = (evidence.source_label or "").strip().lower()
    if source_label in CLAIM_EVIDENCE_SOURCE_WEIGHTS:
        return CLAIM_EVIDENCE_SOURCE_WEIGHTS[source_label]

    hostname = (urlparse(evidence.url).hostname or "").lower()
    for suffix, weight in CLAIM_EVIDENCE_DOMAIN_WEIGHTS.items():
        if hostname.endswith(suffix) or hostname == suffix:
            return weight
    return 1.0


def compute_evidence_score(*, evidence_items):
    total = 0.0
    for evidence in evidence_items:
        source_weight = _evidence_source_weight(evidence)
        citation_factor = 1.0 + math.log1p(getattr(evidence, "citation_count", 0))
        trust_score = getattr(evidence, "trust_score", CLAIM_EVIDENCE_DEFAULT_TRUST)
        trust_multiplier = min(max(trust_score, 0.0), CLAIM_EVIDENCE_MAX_TRUST)
        credibility_weight = (
            source_weight * citation_factor * max(trust_multiplier, 0.1)
        )
        total += math.log1p(credibility_weight)
    return total


def _build_pagerank_edges(claims, relation_weights):
    edge_map = {claim.pk: [] for claim in claims}
    outbound_weight_totals = {claim.pk: 0.0 for claim in claims}

    for claim in claims:
        for relation in claim.outgoing_relations.all():
            relation_weight = relation_weights.get(relation.relation_type.code)
            if relation_weight is None:
                continue
            edge_map[claim.pk].append((relation.target_claim_id, relation_weight))
            outbound_weight_totals[claim.pk] += relation_weight
    return edge_map, outbound_weight_totals


def compute_pagerank_scores(*, claims):
    claim_list = list(claims)
    if not claim_list:
        return {}

    claim_ids = [claim.pk for claim in claim_list]
    count = len(claim_list)
    base_rank = 1.0 / count
    support_edges, support_totals = _build_pagerank_edges(
        claim_list, CLAIM_SUPPORT_RELATION_WEIGHTS
    )
    oppose_edges, oppose_totals = _build_pagerank_edges(
        claim_list, CLAIM_OPPOSE_RELATION_WEIGHTS
    )

    support_rank = {claim_id: base_rank for claim_id in claim_ids}
    oppose_rank = {claim_id: base_rank for claim_id in claim_ids}

    def iterate(rank_map, edge_map, outbound_totals):
        for _ in range(CLAIM_PAGERANK_MAX_ITERATIONS):
            next_rank = {
                claim_id: (1.0 - CLAIM_PAGERANK_DAMPING) / count
                for claim_id in claim_ids
            }
            dangling_total = 0.0
            for source_id in claim_ids:
                total_weight = outbound_totals[source_id]
                if total_weight <= 0.0:
                    dangling_total += rank_map[source_id]
                    continue
                for target_id, relation_weight in edge_map[source_id]:
                    next_rank[target_id] += (
                        CLAIM_PAGERANK_DAMPING
                        * rank_map[source_id]
                        * (relation_weight / total_weight)
                    )
            dangling_share = CLAIM_PAGERANK_DAMPING * dangling_total / count
            if dangling_share:
                for claim_id in claim_ids:
                    next_rank[claim_id] += dangling_share
            delta = sum(
                abs(next_rank[claim_id] - rank_map[claim_id]) for claim_id in claim_ids
            )
            rank_map = next_rank
            if delta <= CLAIM_PAGERANK_TOLERANCE:
                break
        return rank_map

    support_rank = iterate(support_rank, support_edges, support_totals)
    oppose_rank = iterate(oppose_rank, oppose_edges, oppose_totals)

    return {
        claim_id: {
            "support_score": support_rank[claim_id],
            "oppose_score": oppose_rank[claim_id],
            "pagerank_score": support_rank[claim_id] - oppose_rank[claim_id],
        }
        for claim_id in claim_ids
    }


def calculate_claim_score(claim):
    thesis_scores = calculate_thesis_claim_scores(claim.thesis)
    return thesis_scores.get(claim.pk)


def calculate_thesis_claim_scores(thesis):
    claims = list(_claim_score_queryset(thesis=thesis))
    if not claims:
        ClaimScore.objects.filter(claim__thesis=thesis).delete()
        return {}
    claim_lookup = {claim.pk: claim for claim in claims}
    for claim in claims:
        for relation in claim.outgoing_relations.all():
            relation.target_claim = claim_lookup.get(
                relation.target_claim_id,
                relation.target_claim,
            )

    pagerank_map = compute_pagerank_scores(claims=claims)
    score_instances = {}
    active_ids = {claim.pk for claim in claims}

    for claim in claims:
        upvotes = getattr(claim, "upvote_count", 0)
        downvotes = getattr(claim, "downvote_count", 0)
        vote_score = float(upvotes - downvotes)
        bayesian_vote_score = compute_bayesian_vote_score(
            upvotes=upvotes,
            downvotes=downvotes,
        )
        evidence_score = compute_evidence_score(
            evidence_items=list(claim.evidence_items.all()),
        )
        pagerank_metrics = pagerank_map.get(
            claim.pk,
            {
                "support_score": 0.0,
                "oppose_score": 0.0,
                "pagerank_score": 0.0,
            },
        )
        final_score = (
            bayesian_vote_score
            + evidence_score
            + pagerank_metrics["pagerank_score"]
        )
        score, _created = ClaimScore.objects.update_or_create(
            claim=claim,
            defaults={
                "vote_score": vote_score,
                "bayesian_vote_score": bayesian_vote_score,
                "evidence_score": evidence_score,
                "support_score": pagerank_metrics["support_score"],
                "oppose_score": pagerank_metrics["oppose_score"],
                "graph_score": pagerank_metrics["pagerank_score"],
                "pagerank_score": pagerank_metrics["pagerank_score"],
                "final_score": final_score,
            },
        )
        claim.score = score
        score_instances[claim.pk] = score

    ClaimScore.objects.exclude(claim_id__in=active_ids).filter(
        claim__thesis=thesis
    ).delete()
    return score_instances


def rebuild_claim_scores():
    score_map = {}
    thesis_ids = Claim.objects.order_by().values_list("thesis_id", flat=True).distinct()
    for thesis_id in thesis_ids:
        score_map.update(calculate_thesis_claim_scores(Thesis.objects.get(pk=thesis_id)))
    return score_map


def create_claim_for_thesis(*, form, thesis, author):
    with transaction.atomic():
        claim = form.save(commit=False)
        claim.thesis = thesis
        claim.author = author
        claim.save()

        target_claim = form.cleaned_data.get("target_claim")
        relation_type = form.cleaned_data.get("relation_type")
        if target_claim is not None and relation_type is not None:
            ClaimRelation.objects.create(
                source_claim=target_claim,
                target_claim=claim,
                relation_type=relation_type,
            )
        calculate_thesis_claim_scores(thesis)
    return claim


def add_evidence_to_claim(*, form, claim, created_by):
    evidence = form.save(commit=False)
    evidence.claim = claim
    evidence.created_by = created_by
    evidence.save()
    calculate_thesis_claim_scores(claim.thesis)
    return evidence


def cast_vote_for_claim(*, claim, user, vote_type):
    vote, created = ClaimVote.objects.get_or_create(
        claim=claim,
        user=user,
        defaults={"vote_type": vote_type},
    )
    if not created:
        raise ValidationError("Users can only vote once per claim.")
    calculate_thesis_claim_scores(claim.thesis)
    return vote


def update_claim_with_revision(*, form, claim, edited_by):
    with transaction.atomic():
        previous_body = type(claim).objects.only("body").get(pk=claim.pk).body
        updated_claim = form.save(commit=False)
        if previous_body != updated_claim.body:
            ClaimRevision.objects.create(
                claim=claim,
                previous_body=previous_body,
                edited_by=edited_by,
            )
        updated_claim.save()
        calculate_thesis_claim_scores(claim.thesis)
    return updated_claim


def create_claim_from_argument(*, argument, author):
    if DebateClaimMapping.objects.filter(argument=argument).exists():
        raise ValidationError("Argument is already mapped to an archived claim.")

    with transaction.atomic():
        claim = Claim.objects.create(
            thesis=argument.thesis,
            author=author,
            body=argument.body,
        )
        DebateClaimMapping.objects.create(
            thesis=argument.thesis,
            argument=argument,
            claim=claim,
        )
        calculate_thesis_claim_scores(argument.thesis)
    return claim


def create_claim_from_counter(*, counter, author):
    if DebateClaimMapping.objects.filter(counter=counter).exists():
        raise ValidationError("Counter is already mapped to an archived claim.")

    with transaction.atomic():
        claim = Claim.objects.create(
            thesis=counter.thesis,
            author=author,
            body=counter.body,
        )
        DebateClaimMapping.objects.create(
            thesis=counter.thesis,
            counter=counter,
            claim=claim,
        )

        parent_mapping = None
        if counter.parent_counter_id:
            parent_mapping = (
                DebateClaimMapping.objects.select_related("claim")
                .filter(counter=counter.parent_counter)
                .first()
            )
        if parent_mapping is None:
            parent_mapping = (
                DebateClaimMapping.objects.select_related("claim")
                .filter(argument=counter.target_argument)
                .first()
            )
        if parent_mapping is not None:
            oppose_type = ClaimRelationType.objects.get(code=ClaimRelationType.OPPOSE)
            ClaimRelation.objects.get_or_create(
                source_claim=parent_mapping.claim,
                target_claim=claim,
                relation_type=oppose_type,
            )
        calculate_thesis_claim_scores(counter.thesis)
    return claim


def merge_claims(*, source_claim, target_claim, admin_user, reason=""):
    validate_claim_merge(source_claim=source_claim, target_claim=target_claim)

    if ClaimCanonical.objects.filter(claim=source_claim).exists():
        raise ValidationError("Source claim is already merged into a canonical claim.")

    if ClaimCanonical.objects.filter(claim=target_claim).exists():
        raise ValidationError("Target claim is already merged into another claim.")

    touching_relations = list(
        ClaimRelation.objects.filter(
            Q(source_claim=source_claim) | Q(target_claim=source_claim)
        ).select_related("relation_type", "source_claim", "target_claim")
    )
    edge_pairs = set()
    for relation in touching_relations:
        new_source_id = (
            target_claim.id
            if relation.source_claim_id == source_claim.id
            else relation.source_claim_id
        )
        new_target_id = (
            target_claim.id
            if relation.target_claim_id == source_claim.id
            else relation.target_claim_id
        )
        if new_source_id == new_target_id:
            continue
        edge_pairs.add((new_source_id, new_target_id))

    validate_claim_merge_graph(
        thesis_id=source_claim.thesis_id,
        source_claim_id=source_claim.id,
        target_claim_id=target_claim.id,
        edge_pairs=edge_pairs,
    )

    with transaction.atomic():
        for relation in touching_relations:
            new_source_id = (
                target_claim.id
                if relation.source_claim_id == source_claim.id
                else relation.source_claim_id
            )
            new_target_id = (
                target_claim.id
                if relation.target_claim_id == source_claim.id
                else relation.target_claim_id
            )
            if new_source_id == new_target_id:
                relation.delete()
                continue
            duplicate_exists = ClaimRelation.objects.filter(
                source_claim_id=new_source_id,
                target_claim_id=new_target_id,
                relation_type_id=relation.relation_type_id,
            ).exclude(pk=relation.pk).exists()
            if duplicate_exists:
                relation.delete()
            else:
                ClaimRelation.objects.filter(pk=relation.pk).update(
                    source_claim_id=new_source_id,
                    target_claim_id=new_target_id,
                )

        ClaimEvidence.objects.filter(claim=source_claim).update(claim=target_claim)
        ClaimRevision.objects.filter(claim=source_claim).update(claim=target_claim)
        DebateClaimMapping.objects.filter(claim=source_claim).update(claim=target_claim)

        for vote in list(ClaimVote.objects.filter(claim=source_claim)):
            duplicate_vote = ClaimVote.objects.filter(
                claim=target_claim,
                user=vote.user,
            ).exists()
            if duplicate_vote:
                vote.delete()
            else:
                ClaimVote.objects.filter(pk=vote.pk).update(claim=target_claim)

        ClaimCanonical.objects.update_or_create(
            claim=source_claim,
            defaults={"canonical_claim": target_claim},
        )
        ClaimCanonical.objects.filter(canonical_claim=source_claim).update(
            canonical_claim=target_claim
        )
        ClaimSimilarity = source_claim.similarity_left.model
        ClaimSimilarity.objects.filter(
            Q(claim_a=source_claim) | Q(claim_b=source_claim)
        ).delete()
        try:
            source_claim.embedding.delete()
        except Claim.embedding.RelatedObjectDoesNotExist:
            pass
        ClaimMergeLog.objects.create(
            source_claim=source_claim,
            target_claim=target_claim,
            merged_by=admin_user,
            reason=reason,
        )
        ClaimScore.objects.filter(claim=source_claim).delete()
        source_claim.status = ContentStatus.ARCHIVED
        source_claim.save(update_fields=["status", "updated_at"])
        calculate_thesis_claim_scores(target_claim.thesis)
        refresh_claim_similarity(claim=target_claim)
        rebuild_thesis_inference_safe(thesis=target_claim.thesis)

    return target_claim

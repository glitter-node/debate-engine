from collections import defaultdict

from django.db.models import Count, Q

from ..content_status import ContentStatus
from ..models import (
    Argument,
    Claim,
    ClaimCanonical,
    ClaimContradiction,
    ClaimDuplicateReview,
    ClaimInference,
    ClaimNormalized,
    ClaimSimilarity,
    ClaimMergeLog,
    ClaimScore,
    ClaimSupportClosure,
    ClaimTriple,
    ClaimVote,
    Counter,
)


def build_claim_graph(claims):
    claim_list = list(claims)
    by_target = defaultdict(list)
    for claim in claim_list:
        claim.graph_children = []
        claim.graph_incoming = []
        outgoing = sorted(
            list(claim.outgoing_relations.all()),
            key=lambda relation: (relation.created_at, relation.id),
        )
        claim.graph_outgoing = outgoing
        for relation in outgoing:
            by_target[relation.target_claim_id].append(relation)

    for claim in claim_list:
        claim.graph_incoming = by_target.get(claim.id, [])
        for relation in claim.graph_outgoing:
            relation.child_claim = relation.target_claim
            claim.graph_children.append(relation)

    roots = [claim for claim in claim_list if not claim.graph_incoming]
    if not roots:
        roots = claim_list[:]
    return roots


def claim_vote_totals(claims):
    return {
        claim.id: {
            "upvotes": getattr(claim, "upvote_count", 0),
            "downvotes": getattr(claim, "downvote_count", 0),
            "score": getattr(claim, "upvote_count", 0)
            - getattr(claim, "downvote_count", 0),
        }
        for claim in claims
    }


def claim_user_votes(votes):
    return {vote.claim_id: vote.vote_type for vote in votes}


def claim_evidence_map(claims):
    return {claim.id: list(claim.evidence_items.all()) for claim in claims}


def claim_revision_map(claims):
    return {claim.id: list(claim.revisions.all()) for claim in claims}


def claim_score_map(claims):
    return {
        claim.id: getattr(claim, "score", None)
        for claim in claims
    }


def claim_normalized_map(claims):
    normalized_map = {}
    alias_map = {}
    for claim in claims:
        try:
            normalized_map[claim.id] = claim.normalized
        except ClaimNormalized.DoesNotExist:
            normalized_map[claim.id] = None
        alias_map[claim.id] = list(claim.aliases.all())
    return normalized_map, alias_map


def debate_claim_mapping_maps(mappings):
    argument_map = {}
    counter_map = {}
    for mapping in mappings:
        if mapping.argument_id:
            argument_map[mapping.argument_id] = mapping.claim
        if mapping.counter_id:
            counter_map[mapping.counter_id] = mapping.claim
    return argument_map, counter_map


def unarchived_arguments(*, thesis):
    return Argument.objects.filter(thesis=thesis).exclude(claim_mappings__isnull=False)


def unarchived_counters(*, thesis):
    return Counter.objects.filter(thesis=thesis).exclude(claim_mappings__isnull=False)


def resolve_canonical_claim(claim):
    current = claim
    seen_ids = set()
    while True:
        if current.pk in seen_ids:
            return current
        seen_ids.add(current.pk)
        try:
            canonical = current.canonical_record
        except ClaimCanonical.DoesNotExist:
            return current
        current = canonical.canonical_claim


def canonical_claim_queryset():
    return Claim.objects.exclude(canonical_record__isnull=False)


def canonical_claim_sets():
    canonical_rows = (
        ClaimCanonical.objects.select_related("canonical_claim", "claim")
        .order_by("canonical_claim_id", "created_at")
    )
    grouped = defaultdict(list)
    for row in canonical_rows:
        grouped[row.canonical_claim_id].append(row)
    return grouped


def claim_merge_history(*, claim):
    return ClaimMergeLog.objects.select_related(
        "source_claim",
        "target_claim",
        "merged_by",
    ).filter(Q(source_claim=claim) | Q(target_claim=claim)).order_by("-merged_at")


def claim_merge_candidates(*, search_query=""):
    qs = (
        canonical_claim_queryset()
        .select_related("thesis", "author")
        .annotate(
            evidence_count=Count("evidence_items"),
            vote_count=Count("votes"),
            outgoing_count=Count("outgoing_relations"),
            incoming_count=Count("incoming_relations"),
            upvote_count=Count(
                "votes",
                filter=Q(votes__vote_type=ClaimVote.VoteType.UPVOTE),
            ),
            downvote_count=Count(
                "votes",
                filter=Q(votes__vote_type=ClaimVote.VoteType.DOWNVOTE),
            ),
        )
    )
    if search_query:
        qs = qs.filter(
            Q(body__icontains=search_query)
            | Q(thesis__title__icontains=search_query)
            | Q(author__username__icontains=search_query)
        )
    return qs


def top_claims_by_strength(*, limit=10):
    return (
        ClaimScore.objects.select_related("claim", "claim__thesis", "claim__author")
        .filter(
            claim__status=ContentStatus.ACTIVE,
            claim__canonical_record__isnull=True,
        )
        .order_by("-final_score", "claim_id")[:limit]
    )


def ranked_claims_for_thesis(*, thesis):
    return (
        ClaimScore.objects.select_related("claim", "claim__author")
        .filter(
            claim__thesis=thesis,
            claim__status=ContentStatus.ACTIVE,
            claim__canonical_record__isnull=True,
        )
        .order_by("-final_score", "claim_id")
    )


def claim_rank_position(*, claim):
    ranked_ids = list(
        ranked_claims_for_thesis(thesis=claim.thesis).values_list("claim_id", flat=True)
    )
    try:
        return ranked_ids.index(claim.id) + 1
    except ValueError:
        return None


def top_claims_by_score(*, limit=10):
    return top_claims_by_strength(limit=limit)


def ranked_claims_within_thesis(*, thesis):
    return ranked_claims_for_thesis(thesis=thesis)


def duplicate_claim_candidates(*, thesis=None):
    qs = ClaimSimilarity.objects.select_related(
        "claim_a",
        "claim_a__thesis",
        "claim_a__author",
        "claim_b",
        "claim_b__author",
    ).order_by("-similarity_score", "-detected_at")
    if thesis is not None:
        qs = qs.filter(claim_a__thesis=thesis)
    return qs


def duplicate_claim_suggestions(*, thesis, body, limit=5):
    from ..services.claim_duplicates import (
        compute_cosine_similarity,
        generate_claim_embedding,
        generate_claim_embedding_vector,
    )
    from ..scoring import CLAIM_SIMILARITY_THRESHOLD

    target_vector = generate_claim_embedding_vector(text=body)
    base_claims = Claim.objects.select_related("thesis", "author").filter(
        thesis=thesis,
        status=ContentStatus.ACTIVE,
        canonical_record__isnull=True,
    )[:200]
    results = []
    for embedding in base_claims:
        if not hasattr(embedding, "embedding"):
            generate_claim_embedding(claim=embedding)
            embedding.refresh_from_db()
        similarity_score = compute_cosine_similarity(
            vector_a=target_vector,
            vector_b=embedding.embedding.embedding_vector,
        )
        if similarity_score >= CLAIM_SIMILARITY_THRESHOLD:
            results.append((similarity_score, embedding))
    results.sort(key=lambda item: (-item[0], item[1].id))
    return results[:limit]


def duplicate_claim_reviews():
    return ClaimDuplicateReview.objects.select_related(
        "claim_a",
        "claim_b",
        "reviewed_by",
    ).order_by("-reviewed_at")


def claims_by_entity(*, entity):
    return (
        Claim.objects.select_related("author")
        .filter(
            Q(triples__subject_entity=entity) | Q(triples__object_entity=entity)
        )
        .distinct()
    )


def claims_by_predicate(*, predicate):
    return (
        Claim.objects.select_related("author")
        .filter(triples__predicate=predicate)
        .distinct()
    )


def claim_triples_for_thesis(*, thesis):
    return (
        ClaimTriple.objects.select_related(
            "claim",
            "subject_entity",
            "predicate",
            "object_entity",
        )
        .filter(claim__thesis=thesis)
        .order_by("-confidence", "id")
    )


def related_entity_graph(*, thesis):
    graph = defaultdict(list)
    for triple in claim_triples_for_thesis(thesis=thesis):
        graph[triple.subject_entity.canonical_name].append(
            {
                "predicate": triple.predicate.name,
                "object": triple.object_entity.canonical_name,
                "claim_id": triple.claim_id,
                "confidence": triple.confidence,
            }
        )
    return graph


def inferred_claims_for_claim(*, claim):
    return ClaimInference.objects.select_related(
        "source_claim_a",
        "source_claim_b",
        "inferred_claim",
        "rule",
    ).filter(Q(source_claim_a=claim) | Q(source_claim_b=claim)).order_by(
        "-confidence",
        "-created_at",
    )


def contradictions_for_claim(*, claim):
    return ClaimContradiction.objects.select_related(
        "claim_a",
        "claim_b",
    ).filter(Q(claim_a=claim) | Q(claim_b=claim)).order_by("-confidence")


def support_closure_graph(*, thesis):
    return (
        ClaimSupportClosure.objects.select_related("source_claim", "target_claim")
        .filter(source_claim__thesis=thesis)
        .order_by("support_depth", "-confidence", "source_claim_id", "target_claim_id")
    )


def claim_inference_map(*, thesis):
    result = defaultdict(list)
    for inference in (
        ClaimInference.objects.select_related(
            "source_claim_a",
            "source_claim_b",
            "inferred_claim",
            "rule",
        )
        .filter(source_claim_a__thesis=thesis)
        .order_by("-confidence", "-created_at")
    ):
        result[inference.source_claim_a_id].append(inference)
        result[inference.source_claim_b_id].append(inference)
    return result


def claim_contradiction_map(*, thesis):
    result = defaultdict(list)
    for contradiction in contradictions_for_thesis(thesis=thesis):
        result[contradiction.claim_a_id].append(contradiction)
        result[contradiction.claim_b_id].append(contradiction)
    return result


def contradictions_for_thesis(*, thesis):
    return ClaimContradiction.objects.select_related("claim_a", "claim_b").filter(
        claim_a__thesis=thesis
    ).order_by("-confidence", "claim_a_id", "claim_b_id")


def claim_support_closure_map(*, thesis):
    result = defaultdict(list)
    for row in support_closure_graph(thesis=thesis):
        result[row.source_claim_id].append(row)
    return result


def build_legacy_claim_records(*, thesis, arguments, counters_by_argument_map):
    argument_lookup = {}
    records = []
    for argument in arguments:
        record = {
            "node_id": f"argument-{argument.id}",
            "body": argument.body,
            "kind": "argument",
            "relation_label": "Supports thesis",
            "children": [],
        }
        argument_lookup[argument.id] = record
        records.append(record)

    def build_counter_record(counter):
        children = [build_counter_record(child) for child in counter.rebuttal_children]
        relation_label = (
            "Opposes argument"
            if counter.parent_counter_id is None
            else "Opposes parent claim"
        )
        return {
            "node_id": f"counter-{counter.id}",
            "body": counter.body,
            "kind": "counter",
            "relation_label": relation_label,
            "children": children,
        }

    for argument in arguments:
        parent_record = argument_lookup[argument.id]
        for counter in counters_by_argument_map.get(argument.id, []):
            parent_record["children"].append(build_counter_record(counter))
    return records

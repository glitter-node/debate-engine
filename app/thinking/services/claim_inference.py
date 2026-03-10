from collections import defaultdict, deque
from threading import local

from django.db import transaction

from ..content_status import ContentStatus
from ..models import (
    Claim,
    ClaimContradiction,
    ClaimInference,
    ClaimInferenceRule,
    ClaimNormalized,
    ClaimRelation,
    ClaimRelationType,
    ClaimSupportClosure,
    Thesis,
)
from .claim_normalization import (
    get_or_create_claim_predicate,
    normalize_claim_safe,
)

DEFAULT_INFERENCE_RULES = (
    {
        "name": "causal-chain-increases-risk",
        "pattern_predicate_a": "cause",
        "pattern_predicate_b": "cause",
        "inferred_predicate": "increase-risk",
        "confidence_weight": 0.78,
    },
    {
        "name": "preventive-chain-reduces-risk",
        "pattern_predicate_a": "prevent",
        "pattern_predicate_b": "cause",
        "inferred_predicate": "prevent",
        "confidence_weight": 0.72,
    },
)

PREDICATE_TEXT = {
    "cause": "causes",
    "prevent": "prevents",
    "support": "supports",
    "oppose": "opposes",
    "be": "is",
    "increase-risk": "increases the risk of",
}

CONTRADICTORY_PREDICATES = {
    frozenset(("cause", "prevent")): ("causal-conflict", 0.9),
    frozenset(("support", "oppose")): ("stance-conflict", 0.85),
    frozenset(("be", "oppose")): ("identity-conflict", 0.6),
}

_INFERENCE_STATE = local()


def _active_theory_ids():
    active_ids = getattr(_INFERENCE_STATE, "theory_ids", None)
    if active_ids is None:
        active_ids = set()
        _INFERENCE_STATE.theory_ids = active_ids
    return active_ids


def ensure_default_inference_rules():
    rules = []
    for payload in DEFAULT_INFERENCE_RULES:
        rule, _created = ClaimInferenceRule.objects.get_or_create(
            name=payload["name"],
            defaults=payload,
        )
        rules.append(rule)
    get_or_create_claim_predicate(name="increase-risk")
    return rules


def render_inferred_claim_body(*, subject_name, predicate_name, object_name):
    predicate_text = PREDICATE_TEXT.get(
        predicate_name,
        predicate_name.replace("-", " "),
    )
    return f"{subject_name} {predicate_text} {object_name}"


def _base_triples_for_thesis(*, thesis):
    normalized_rows = (
        ClaimNormalized.objects.select_related(
            "claim",
            "claim__author",
            "triple",
            "triple__subject_entity",
            "triple__predicate",
            "triple__object_entity",
        )
        .filter(
            claim__thesis=thesis,
            claim__status=ContentStatus.ACTIVE,
            claim__canonical_record__isnull=True,
            claim__generated_inferences__isnull=True,
        )
        .order_by("claim_id")
    )
    return list(normalized_rows)


def _support_relation_type():
    relation_type, _created = ClaimRelationType.objects.get_or_create(
        code=ClaimRelationType.SUPPORT,
        defaults={"label": "Support"},
    )
    return relation_type


def _claim_for_inferred_body(*, thesis, author, body):
    claim = (
        Claim.objects.filter(
            thesis=thesis,
            body=body,
            canonical_record__isnull=True,
        )
        .order_by("id")
        .first()
    )
    if claim is not None:
        return claim
    return Claim.objects.create(
        thesis=thesis,
        author=author,
        body=body,
        status=ContentStatus.ACTIVE,
    )


def _canonical_source_pair(*, claim_a, claim_b):
    if claim_a.pk <= claim_b.pk:
        return claim_a, claim_b
    return claim_b, claim_a


def rebuild_claim_inferences(*, thesis):
    ensure_default_inference_rules()
    support_type = _support_relation_type()
    rules = {
        (rule.pattern_predicate_a, rule.pattern_predicate_b): rule
        for rule in ClaimInferenceRule.objects.all()
    }
    normalized_rows = _base_triples_for_thesis(thesis=thesis)
    by_subject = defaultdict(list)
    valid_keys = set()
    created_rows = []

    for normalized in normalized_rows:
        by_subject[normalized.triple.subject_entity_id].append(normalized)

    with transaction.atomic():
        for normalized_a in normalized_rows:
            triple_a = normalized_a.triple
            for normalized_b in by_subject.get(triple_a.object_entity_id, []):
                if normalized_a.claim_id == normalized_b.claim_id:
                    continue
                triple_b = normalized_b.triple
                rule = rules.get(
                    (triple_a.predicate.name, triple_b.predicate.name)
                )
                if rule is None:
                    continue
                body = render_inferred_claim_body(
                    subject_name=triple_a.subject_entity.canonical_name,
                    predicate_name=rule.inferred_predicate,
                    object_name=triple_b.object_entity.canonical_name,
                )
                inferred_claim = _claim_for_inferred_body(
                    thesis=thesis,
                    author=normalized_a.claim.author,
                    body=body,
                )
                if inferred_claim.pk in {normalized_a.claim_id, normalized_b.claim_id}:
                    continue
                normalize_claim_safe(claim=inferred_claim)
                source_claim_a, source_claim_b = _canonical_source_pair(
                    claim_a=normalized_a.claim,
                    claim_b=normalized_b.claim,
                )
                confidence = (
                    min(normalized_a.confidence, normalized_b.confidence)
                    * rule.confidence_weight
                )
                inference, _created = ClaimInference.objects.update_or_create(
                    source_claim_a=source_claim_a,
                    source_claim_b=source_claim_b,
                    inferred_claim=inferred_claim,
                    rule=rule,
                    defaults={"confidence": confidence},
                )
                valid_keys.add(inference.pk)
                created_rows.append(inference)
                for source_claim in (normalized_a.claim, normalized_b.claim):
                    ClaimRelation.objects.get_or_create(
                        source_claim=source_claim,
                        target_claim=inferred_claim,
                        relation_type=support_type,
                    )

        stale_qs = ClaimInference.objects.filter(source_claim_a__thesis=thesis)
        if valid_keys:
            stale_qs = stale_qs.exclude(pk__in=valid_keys)
        stale_qs.delete()
    return created_rows


def rebuild_claim_contradictions(*, thesis):
    contradictions = []
    normalized_rows = list(
        ClaimNormalized.objects.select_related(
            "claim",
            "triple",
            "triple__subject_entity",
            "triple__predicate",
            "triple__object_entity",
        ).filter(
            claim__thesis=thesis,
            claim__status=ContentStatus.ACTIVE,
            claim__canonical_record__isnull=True,
        )
    )
    ClaimContradiction.objects.filter(claim_a__thesis=thesis).delete()
    for index, left in enumerate(normalized_rows):
        left_triple = left.triple
        for right in normalized_rows[index + 1 :]:
            right_triple = right.triple
            if (
                left_triple.subject_entity_id != right_triple.subject_entity_id
                or left_triple.object_entity_id != right_triple.object_entity_id
            ):
                continue
            contradiction_meta = CONTRADICTORY_PREDICATES.get(
                frozenset(
                    (left_triple.predicate.name, right_triple.predicate.name)
                )
            )
            if contradiction_meta is None:
                continue
            contradiction_type, weight = contradiction_meta
            claim_a, claim_b = _canonical_source_pair(
                claim_a=left.claim,
                claim_b=right.claim,
            )
            contradictions.append(
                ClaimContradiction.objects.create(
                    claim_a=claim_a,
                    claim_b=claim_b,
                    contradiction_type=contradiction_type,
                    confidence=min(left.confidence, right.confidence) * weight,
                )
            )
    return contradictions


def rebuild_claim_support_closure(*, thesis):
    support_type = _support_relation_type()
    adjacency = defaultdict(list)
    direct_relations = list(
        ClaimRelation.objects.filter(
            source_claim__thesis=thesis,
            source_claim__status=ContentStatus.ACTIVE,
            source_claim__canonical_record__isnull=True,
            target_claim__status=ContentStatus.ACTIVE,
            target_claim__canonical_record__isnull=True,
            relation_type=support_type,
        )
    )
    for relation in direct_relations:
        adjacency[relation.source_claim_id].append(relation.target_claim_id)
    ClaimSupportClosure.objects.filter(source_claim__thesis=thesis).delete()
    created = []
    for source_id, direct_targets in adjacency.items():
        queue = deque((target_id, 1) for target_id in direct_targets)
        seen_depths = {}
        while queue:
            target_id, depth = queue.popleft()
            if source_id == target_id:
                continue
            previous_depth = seen_depths.get(target_id)
            if previous_depth is not None and previous_depth <= depth:
                continue
            seen_depths[target_id] = depth
            created.append(
                ClaimSupportClosure.objects.create(
                    source_claim_id=source_id,
                    target_claim_id=target_id,
                    support_depth=depth,
                    confidence=1.0 / depth,
                )
            )
            for next_target_id in adjacency.get(target_id, []):
                if next_target_id != source_id:
                    queue.append((next_target_id, depth + 1))
    return created


def rebuild_thesis_inference(*, thesis):
    with transaction.atomic():
        inferences = rebuild_claim_inferences(thesis=thesis)
        contradictions = rebuild_claim_contradictions(thesis=thesis)
        support_closure = rebuild_claim_support_closure(thesis=thesis)
    return {
        "inferences": inferences,
        "contradictions": contradictions,
        "support_closure": support_closure,
    }


def rebuild_thesis_inference_safe(*, thesis):
    theory_ids = _active_theory_ids()
    if thesis.pk in theory_ids:
        return None
    theory_ids.add(thesis.pk)
    try:
        return rebuild_thesis_inference(thesis=thesis)
    finally:
        theory_ids.discard(thesis.pk)


def rebuild_all_inference():
    ensure_default_inference_rules()
    return {
        thesis.pk: rebuild_thesis_inference_safe(thesis=thesis)
        for thesis in Thesis.objects.all()
    }

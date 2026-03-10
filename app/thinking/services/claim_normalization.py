import re

from django.db import transaction

from ..models import (
    Claim,
    ClaimAlias,
    ClaimEntity,
    ClaimNormalized,
    ClaimPredicate,
    ClaimTriple,
)

PREDICATE_PATTERNS = (
    (r"^(?P<subject>.+?)\s+causes?\s+(?P<object>.+)$", "cause", 0.92),
    (
        r"^(?P<subject>.+?)\s+increases?\s+(?:the\s+)?risk\s+of\s+(?P<object>.+)$",
        "cause",
        0.9,
    ),
    (r"^(?P<subject>.+?)\s+leads?\s+to\s+(?P<object>.+)$", "cause", 0.86),
    (r"^(?P<subject>.+?)\s+prevents?\s+(?P<object>.+)$", "prevent", 0.88),
    (r"^(?P<subject>.+?)\s+supports?\s+(?P<object>.+)$", "support", 0.8),
    (r"^(?P<subject>.+?)\s+opposes?\s+(?P<object>.+)$", "oppose", 0.8),
    (r"^(?P<subject>.+?)\s+is\s+(?P<object>.+)$", "be", 0.55),
)

LEADING_ARTICLE_PATTERN = re.compile(r"^(the|a|an)\s+")
SPACE_PATTERN = re.compile(r"\s+")
ENTITY_SYNONYMS = {
    "cigarette smoking": "smoking",
    "smoking tobacco": "smoking",
    "tumors": "cancer",
    "tumor": "cancer",
}


def canonicalize_entity_name(name):
    normalized = (name or "").strip().lower()
    normalized = LEADING_ARTICLE_PATTERN.sub("", normalized)
    normalized = re.sub(r"[^\w\s-]", "", normalized)
    normalized = SPACE_PATTERN.sub(" ", normalized).strip()
    return ENTITY_SYNONYMS.get(normalized, normalized)


def get_or_create_claim_entity(*, name, entity_type="concept"):
    canonical_name = canonicalize_entity_name(name)
    entity, _created = ClaimEntity.objects.get_or_create(
        canonical_name=canonical_name,
        defaults={
            "name": name.strip(),
            "entity_type": entity_type,
        },
    )
    return entity


def get_or_create_claim_predicate(*, name):
    predicate, _created = ClaimPredicate.objects.get_or_create(name=name)
    return predicate


def parse_claim_text_to_triple(*, claim_text):
    text = SPACE_PATTERN.sub(" ", (claim_text or "").strip())
    for pattern, predicate_name, confidence in PREDICATE_PATTERNS:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        subject = match.group("subject").strip(" .")
        object_text = match.group("object").strip(" .")
        if subject and object_text:
            return {
                "subject": subject,
                "predicate": predicate_name,
                "object": object_text,
                "confidence": confidence,
            }
    return None


def normalize_claim(*, claim):
    parsed = parse_claim_text_to_triple(claim_text=claim.body)
    if parsed is None:
        return None

    with transaction.atomic():
        subject_entity = get_or_create_claim_entity(name=parsed["subject"])
        object_entity = get_or_create_claim_entity(name=parsed["object"])
        predicate = get_or_create_claim_predicate(name=parsed["predicate"])
        triple, _created = ClaimTriple.objects.update_or_create(
            claim=claim,
            defaults={
                "subject_entity": subject_entity,
                "predicate": predicate,
                "object_entity": object_entity,
                "confidence": parsed["confidence"],
            },
        )
        normalized, _created = ClaimNormalized.objects.update_or_create(
            claim=claim,
            defaults={
                "triple": triple,
                "normalization_method": "rule-based-v1",
                "confidence": parsed["confidence"],
            },
        )
        alias_texts = {
            parsed["subject"].strip(),
            parsed["object"].strip(),
        }
        for alias_text in alias_texts:
            if alias_text:
                ClaimAlias.objects.get_or_create(
                    claim=claim,
                    alias_text=alias_text,
                )
    return normalized


def normalize_claim_safe(*, claim):
    try:
        return normalize_claim(claim=claim)
    except Exception:
        return None


def rebuild_claim_normalizations(*, thesis=None):
    claims = Claim.objects.all()
    if thesis is not None:
        claims = claims.filter(thesis=thesis)
    results = {}
    for claim in claims:
        results[claim.pk] = normalize_claim_safe(claim=claim)
    return results

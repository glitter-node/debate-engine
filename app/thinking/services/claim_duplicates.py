import math
import re
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db import models

from ..content_status import ContentStatus
from ..models import (
    Claim,
    ClaimCanonical,
    ClaimDuplicateReview,
    ClaimEmbedding,
    ClaimSimilarity,
)
from ..scoring import (
    CLAIM_EMBEDDING_DIMENSIONS,
    CLAIM_EMBEDDING_MODEL,
    CLAIM_SIMILARITY_THRESHOLD,
)

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
TOKEN_SYNONYMS = {
    "cigarette": "smoking",
    "cigarettes": "smoking",
    "smoker": "smoking",
    "smokers": "smoking",
    "smoking": "smoking",
    "causes": "cause",
    "caused": "cause",
    "cause": "cause",
    "increases": "cause",
    "increased": "cause",
    "increase": "cause",
    "risk": "cause",
    "risks": "cause",
    "cancer": "cancer",
    "tumor": "cancer",
    "tumors": "cancer",
}
TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}


def _canonical_claim_pair(claim_a, claim_b):
    return (claim_a, claim_b) if claim_a.pk < claim_b.pk else (claim_b, claim_a)


def _normalized_tokens(text):
    tokens = TOKEN_PATTERN.findall((text or "").lower())
    normalized = []
    for token in tokens:
        normalized_token = TOKEN_SYNONYMS.get(token, token)
        if normalized_token in TOKEN_STOPWORDS:
            continue
        normalized.append(normalized_token)
    return normalized


def _hash_index(token):
    total = 0
    for character in token:
        total = (total * 33 + ord(character)) % CLAIM_EMBEDDING_DIMENSIONS
    return total


def generate_claim_embedding_vector(*, text):
    tokens = _normalized_tokens(text)
    counts = Counter(tokens)
    bigram_counts = Counter(
        f"{tokens[index]}::{tokens[index + 1]}"
        for index in range(len(tokens) - 1)
    )
    vector = [0.0] * CLAIM_EMBEDDING_DIMENSIONS
    for token, count in counts.items():
        vector[_hash_index(token)] += float(count)
    for bigram, count in bigram_counts.items():
        vector[_hash_index(bigram)] += float(count) * 0.5
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude > 0.0:
        vector = [value / magnitude for value in vector]
    return vector


def compute_cosine_similarity(*, vector_a, vector_b):
    if not vector_a or not vector_b:
        return 0.0
    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    magnitude_a = math.sqrt(sum(a * a for a in vector_a))
    magnitude_b = math.sqrt(sum(b * b for b in vector_b))
    if magnitude_a <= 0.0 or magnitude_b <= 0.0:
        return 0.0
    return dot_product / (magnitude_a * magnitude_b)


def generate_claim_embedding(*, claim):
    vector = generate_claim_embedding_vector(text=claim.body)
    embedding, _created = ClaimEmbedding.objects.update_or_create(
        claim=claim,
        defaults={
            "embedding_vector": vector,
            "embedding_model": CLAIM_EMBEDDING_MODEL,
        },
    )
    return embedding


def refresh_claim_similarity(*, claim):
    if claim.status != ContentStatus.ACTIVE:
        ClaimEmbedding.objects.filter(claim=claim).delete()
        ClaimSimilarity.objects.filter(claim_a=claim).delete()
        ClaimSimilarity.objects.filter(claim_b=claim).delete()
        return []
    if ClaimCanonical.objects.filter(claim=claim).exists():
        ClaimSimilarity.objects.filter(claim_a=claim).delete()
        ClaimSimilarity.objects.filter(claim_b=claim).delete()
        return []

    embedding = generate_claim_embedding(claim=claim)
    ClaimSimilarity.objects.filter(claim_a=claim).delete()
    ClaimSimilarity.objects.filter(claim_b=claim).delete()

    candidate_embeddings = (
        ClaimEmbedding.objects.select_related("claim")
        .filter(
            claim__thesis=claim.thesis,
            claim__status=ContentStatus.ACTIVE,
            claim__canonical_record__isnull=True,
        )
        .exclude(claim=claim)
    )
    created_similarities = []
    for candidate in candidate_embeddings:
        similarity_score = compute_cosine_similarity(
            vector_a=embedding.embedding_vector,
            vector_b=candidate.embedding_vector,
        )
        claim_a, claim_b = _canonical_claim_pair(claim, candidate.claim)
        ignored = ClaimDuplicateReview.objects.filter(
            claim_a=claim_a,
            claim_b=claim_b,
            decision=ClaimDuplicateReview.Decision.IGNORE,
        ).exists()
        if ignored:
            ClaimSimilarity.objects.filter(claim_a=claim_a, claim_b=claim_b).delete()
            continue
        if similarity_score >= CLAIM_SIMILARITY_THRESHOLD:
            similarity, _created = ClaimSimilarity.objects.update_or_create(
                claim_a=claim_a,
                claim_b=claim_b,
                defaults={"similarity_score": similarity_score},
            )
            created_similarities.append(similarity)
        else:
            ClaimSimilarity.objects.filter(
                claim_a=claim_a,
                claim_b=claim_b,
            ).delete()
    return created_similarities


def rebuild_claim_similarities(*, thesis=None):
    claims = Claim.objects.filter(
        status=ContentStatus.ACTIVE,
        canonical_record__isnull=True,
    )
    if thesis is not None:
        claims = claims.filter(thesis=thesis)
        ClaimSimilarity.objects.filter(claim_a__thesis=thesis).delete()
        ClaimEmbedding.objects.filter(claim__thesis=thesis).delete()
    else:
        ClaimSimilarity.objects.all().delete()
        ClaimEmbedding.objects.all().delete()
    results = {}
    for claim in claims.select_related("thesis"):
        results[claim.pk] = refresh_claim_similarity(claim=claim)
    return results


def duplicate_candidates_for_claim(*, claim, limit=5):
    return (
        ClaimSimilarity.objects.select_related("claim_a", "claim_b")
        .filter(claim_a__thesis=claim.thesis)
        .filter(models.Q(claim_a=claim) | models.Q(claim_b=claim))
        .order_by("-similarity_score", "-detected_at")[:limit]
    )


def review_duplicate_pair(
    *,
    claim_a,
    claim_b,
    decision,
    reviewed_by,
    reason="",
    merge_func=None,
):
    claim_a, claim_b = _canonical_claim_pair(claim_a, claim_b)
    if claim_a.thesis_id != claim_b.thesis_id:
        raise ValidationError(
            "Duplicate review pairs must stay within the same thesis."
        )
    with transaction.atomic():
        review, _created = ClaimDuplicateReview.objects.update_or_create(
            claim_a=claim_a,
            claim_b=claim_b,
            defaults={
                "decision": decision,
                "reviewed_by": reviewed_by,
            },
        )
        if decision == ClaimDuplicateReview.Decision.IGNORE:
            ClaimSimilarity.objects.filter(claim_a=claim_a, claim_b=claim_b).delete()
        elif decision == ClaimDuplicateReview.Decision.MERGE:
            if merge_func is None:
                raise ValidationError(
                    "A merge function is required for merge decisions."
                )
            merge_func(
                source_claim=claim_b,
                target_claim=claim_a,
                admin_user=reviewed_by,
                reason=reason or "semantic duplicate review",
            )
            ClaimSimilarity.objects.filter(
                models.Q(claim_a=claim_a) | models.Q(claim_b=claim_a)
            ).delete()
            refresh_claim_similarity(claim=claim_a)
        return review

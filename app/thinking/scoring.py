"""Configuration for advanced claim ranking."""

from thinking.models.claim import ClaimRelationType

CLAIM_BAYESIAN_PRIOR = 2.0

CLAIM_EVIDENCE_DEFAULT_TRUST = 1.0
CLAIM_EVIDENCE_MAX_TRUST = 5.0
CLAIM_EVIDENCE_SOURCE_WEIGHTS = {
    "peer_reviewed": 2.5,
    "government": 2.0,
    "academic": 1.8,
    "journalism": 1.35,
    "reference": 1.2,
    "community": 0.8,
}
CLAIM_EVIDENCE_DOMAIN_WEIGHTS = {
    ".gov": 2.0,
    ".edu": 1.8,
    "wikipedia.org": 1.2,
    "arxiv.org": 1.5,
    "nature.com": 2.4,
    "science.org": 2.4,
}

CLAIM_PAGERANK_DAMPING = 0.85
CLAIM_PAGERANK_MAX_ITERATIONS = 50
CLAIM_PAGERANK_TOLERANCE = 1e-6

CLAIM_SUPPORT_RELATION_WEIGHTS = {
    ClaimRelationType.SUPPORT: 1.0,
    ClaimRelationType.CLARIFY: 0.35,
}
CLAIM_OPPOSE_RELATION_WEIGHTS = {
    ClaimRelationType.OPPOSE: 1.0,
    ClaimRelationType.QUESTION: 0.2,
}

CLAIM_EMBEDDING_MODEL = "local-hash-embedding-v1"
CLAIM_EMBEDDING_DIMENSIONS = 64
CLAIM_SIMILARITY_THRESHOLD = 0.82

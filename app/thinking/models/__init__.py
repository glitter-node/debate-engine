from importlib import import_module

if __name__.startswith("app."):
    _canonical = import_module("thinking.models")

    ActiveManager = _canonical.ActiveManager
    Argument = _canonical.Argument
    AuditLog = _canonical.AuditLog
    Claim = _canonical.Claim
    ClaimAlias = _canonical.ClaimAlias
    ClaimCanonical = _canonical.ClaimCanonical
    ClaimDuplicateReview = _canonical.ClaimDuplicateReview
    ClaimInference = _canonical.ClaimInference
    ClaimInferenceRule = _canonical.ClaimInferenceRule
    ClaimEmbedding = _canonical.ClaimEmbedding
    ClaimEntity = _canonical.ClaimEntity
    ClaimContradiction = _canonical.ClaimContradiction
    DebateClaimMapping = _canonical.DebateClaimMapping
    ClaimEvidence = _canonical.ClaimEvidence
    ClaimMergeLog = _canonical.ClaimMergeLog
    ClaimNormalized = _canonical.ClaimNormalized
    ClaimPredicate = _canonical.ClaimPredicate
    ClaimRelation = _canonical.ClaimRelation
    ClaimRelationType = _canonical.ClaimRelationType
    ClaimSimilarity = _canonical.ClaimSimilarity
    ClaimSupportClosure = _canonical.ClaimSupportClosure
    ClaimTriple = _canonical.ClaimTriple
    ClaimRevision = _canonical.ClaimRevision
    ClaimScore = _canonical.ClaimScore
    ClaimVote = _canonical.ClaimVote
    ContentReport = _canonical.ContentReport
    Counter = _canonical.Counter
    SoftDeleteQuerySet = _canonical.SoftDeleteQuerySet
    Thesis = _canonical.Thesis
    UserRole = _canonical.UserRole
else:
    from .argument import Argument, Counter
    from .audit import AuditLog
    from .claim import (
        Claim,
        ClaimAlias,
        ClaimCanonical,
        ClaimDuplicateReview,
        ClaimInference,
        ClaimInferenceRule,
        ClaimEmbedding,
        ClaimEntity,
        ClaimContradiction,
        DebateClaimMapping,
        ClaimEvidence,
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
    )
    from .moderation import ContentReport
    from .roles import UserRole
    from .thesis import ActiveManager, SoftDeleteQuerySet, Thesis

__all__ = [
    "ActiveManager",
    "Argument",
    "AuditLog",
    "Claim",
    "ClaimAlias",
    "ClaimCanonical",
    "ClaimDuplicateReview",
    "ClaimInference",
    "ClaimInferenceRule",
    "ClaimEmbedding",
    "ClaimEntity",
    "ClaimContradiction",
    "DebateClaimMapping",
    "ClaimEvidence",
    "ClaimMergeLog",
    "ClaimNormalized",
    "ClaimPredicate",
    "ClaimRelation",
    "ClaimRelationType",
    "ClaimSimilarity",
    "ClaimSupportClosure",
    "ClaimTriple",
    "ClaimRevision",
    "ClaimScore",
    "ClaimVote",
    "ContentReport",
    "Counter",
    "SoftDeleteQuerySet",
    "Thesis",
    "UserRole",
]

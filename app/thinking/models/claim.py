from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from ..content_status import CONTENT_STATUS_CHOICES, ContentStatus
from ..domain.chain_validator import validate_claim_relation_edge
from .thesis import Thesis


class ClaimRelationType(models.Model):
    SUPPORT = "support"
    OPPOSE = "oppose"
    CLARIFY = "clarify"
    QUESTION = "question"

    code = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=64)

    class Meta:
        ordering = ["label", "id"]

    def __str__(self):
        return self.label


class Claim(models.Model):
    thesis = models.ForeignKey(Thesis, on_delete=models.CASCADE, related_name="claims")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="claims"
    )
    body = models.TextField()
    status = models.CharField(
        max_length=32,
        choices=CONTENT_STATUS_CHOICES,
        default=ContentStatus.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Claim #{self.pk or 'new'}"


class ClaimRelation(models.Model):
    source_claim = models.ForeignKey(
        Claim, on_delete=models.CASCADE, related_name="outgoing_relations"
    )
    target_claim = models.ForeignKey(
        Claim, on_delete=models.CASCADE, related_name="incoming_relations"
    )
    relation_type = models.ForeignKey(
        ClaimRelationType,
        on_delete=models.PROTECT,
        related_name="claim_relations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_claim", "target_claim", "relation_type"],
                name="uniq_claim_relation_edge",
            ),
        ]

    def __str__(self):
        return (
            f"{self.source_claim_id}->{self.target_claim_id}"
            f" ({self.relation_type_id})"
        )

    def clean(self):
        super().clean()
        if self.source_claim_id == self.target_claim_id:
            raise ValidationError(
                {"target_claim": "Claim relation cannot point to the same claim."}
            )
        if (
            self.source_claim_id
            and self.target_claim_id
            and self.source_claim.thesis_id != self.target_claim.thesis_id
        ):
            raise ValidationError(
                {"target_claim": "Claim relations must stay within the same thesis."}
            )
        if self.source_claim_id and self.target_claim_id:
            validate_claim_relation_edge(
                source_claim=self.source_claim,
                target_claim=self.target_claim,
                relation_id=self.id,
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ClaimEvidence(models.Model):
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="evidence_items",
    )
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=200)
    source_label = models.CharField(max_length=120, blank=True, default="")
    citation_count = models.PositiveIntegerField(default=0, blank=True)
    trust_score = models.FloatField(default=1.0, blank=True)
    excerpt = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="claim_evidence_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.title


class ClaimVote(models.Model):
    class VoteType(models.TextChoices):
        UPVOTE = "upvote", "Upvote"
        DOWNVOTE = "downvote", "Downvote"

    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="claim_votes",
    )
    vote_type = models.CharField(max_length=16, choices=VoteType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["claim", "user"],
                name="uniq_claim_vote_per_user",
            )
        ]

    def __str__(self):
        return f"{self.claim_id}:{self.user_id}:{self.vote_type}"


class ClaimRevision(models.Model):
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    previous_body = models.TextField()
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="claim_revisions",
    )
    edited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-edited_at", "-id"]

    def __str__(self):
        return f"Revision #{self.pk or 'new'} for claim {self.claim_id}"


class ClaimScore(models.Model):
    claim = models.OneToOneField(
        Claim,
        on_delete=models.CASCADE,
        related_name="score",
    )
    vote_score = models.FloatField(default=0.0)
    bayesian_vote_score = models.FloatField(default=0.0)
    evidence_score = models.FloatField(default=0.0)
    support_score = models.FloatField(default=0.0)
    oppose_score = models.FloatField(default=0.0)
    graph_score = models.FloatField(default=0.0)
    pagerank_score = models.FloatField(default=0.0)
    final_score = models.FloatField(default=0.0, db_index=True)
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-final_score", "claim_id"]

    def __str__(self):
        return f"Score for claim {self.claim_id}: {self.final_score:.3f}"


class DebateClaimMapping(models.Model):
    thesis = models.ForeignKey(
        Thesis,
        on_delete=models.CASCADE,
        related_name="claim_mappings",
    )
    argument = models.ForeignKey(
        "thinking.Argument",
        on_delete=models.CASCADE,
        related_name="claim_mappings",
        null=True,
        blank=True,
    )
    counter = models.ForeignKey(
        "thinking.Counter",
        on_delete=models.CASCADE,
        related_name="claim_mappings",
        null=True,
        blank=True,
    )
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="debate_mappings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(argument__isnull=False)
                        & models.Q(counter__isnull=True)
                    )
                    | (
                        models.Q(argument__isnull=True)
                        & models.Q(counter__isnull=False)
                    )
                ),
                name="debate_claim_mapping_exactly_one_source",
            ),
            models.UniqueConstraint(
                fields=["argument"],
                condition=models.Q(argument__isnull=False),
                name="uniq_debate_claim_mapping_argument",
            ),
            models.UniqueConstraint(
                fields=["counter"],
                condition=models.Q(counter__isnull=False),
                name="uniq_debate_claim_mapping_counter",
            ),
        ]

    def __str__(self):
        source = (
            f"argument:{self.argument_id}"
            if self.argument_id
            else f"counter:{self.counter_id}"
        )
        return f"{source} -> claim:{self.claim_id}"

    def clean(self):
        super().clean()
        has_argument = self.argument_id is not None
        has_counter = self.counter_id is not None
        if has_argument == has_counter:
            raise ValidationError(
                "DebateClaimMapping must reference exactly one of argument or counter."
            )
        if self.claim_id and self.thesis_id and self.claim.thesis_id != self.thesis_id:
            raise ValidationError(
                {"claim": "Claim must belong to the selected thesis."}
            )
        if self.argument_id:
            if self.argument.thesis_id != self.thesis_id:
                raise ValidationError(
                    {"argument": "Mapped argument must belong to the selected thesis."}
                )
        if self.counter_id:
            if self.counter.thesis_id != self.thesis_id:
                raise ValidationError(
                    {"counter": "Mapped counter must belong to the selected thesis."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ClaimCanonical(models.Model):
    canonical_claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="canonical_for",
    )
    claim = models.OneToOneField(
        Claim,
        on_delete=models.CASCADE,
        related_name="canonical_record",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["canonical_claim", "created_at"])]

    def __str__(self):
        return f"{self.claim_id} -> {self.canonical_claim_id}"


class ClaimMergeLog(models.Model):
    source_claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="merge_sources",
    )
    target_claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="merge_targets",
    )
    merged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="claim_merge_logs",
    )
    merged_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-merged_at", "-id"]
        indexes = [
            models.Index(fields=["source_claim", "merged_at"]),
            models.Index(fields=["target_claim", "merged_at"]),
        ]

    def __str__(self):
        return f"{self.source_claim_id} => {self.target_claim_id}"


class ClaimEmbedding(models.Model):
    claim = models.OneToOneField(
        Claim,
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    embedding_vector = models.JSONField(default=list)
    embedding_model = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "claim_id"]

    def __str__(self):
        return f"Embedding for claim {self.claim_id}"


class ClaimSimilarity(models.Model):
    claim_a = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="similarity_left",
    )
    claim_b = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="similarity_right",
    )
    similarity_score = models.FloatField(db_index=True)
    detected_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-similarity_score", "-detected_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(claim_a__lt=models.F("claim_b")),
                name="claim_similarity_canonical_pair_order",
            ),
            models.UniqueConstraint(
                fields=["claim_a", "claim_b"],
                name="uniq_claim_similarity_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["claim_a", "-similarity_score"]),
            models.Index(fields=["claim_b", "-similarity_score"]),
        ]

    def __str__(self):
        return (
            f"Similarity {self.claim_a_id}<->{self.claim_b_id}: "
            f"{self.similarity_score:.3f}"
        )

    def clean(self):
        super().clean()
        if self.claim_a_id == self.claim_b_id:
            raise ValidationError("Similarity pair must contain two distinct claims.")
        if self.claim_a_id and self.claim_b_id:
            if self.claim_a_id > self.claim_b_id:
                raise ValidationError(
                    "Similarity pairs must be stored in canonical order."
                )
            if self.claim_a.thesis_id != self.claim_b.thesis_id:
                raise ValidationError(
                    "Similarity pairs must stay within the same thesis."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ClaimDuplicateReview(models.Model):
    class Decision(models.TextChoices):
        MERGE = "merge", "Merge"
        IGNORE = "ignore", "Ignore"

    claim_a = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="duplicate_reviews_left",
    )
    claim_b = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="duplicate_reviews_right",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="claim_duplicate_reviews",
    )
    reviewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reviewed_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(claim_a__lt=models.F("claim_b")),
                name="claim_duplicate_review_canonical_pair_order",
            ),
            models.UniqueConstraint(
                fields=["claim_a", "claim_b"],
                name="uniq_claim_duplicate_review_pair",
            ),
        ]

    def __str__(self):
        return (
            f"Duplicate review {self.claim_a_id}<->{self.claim_b_id}: "
            f"{self.decision}"
        )

    def clean(self):
        super().clean()
        if self.claim_a_id == self.claim_b_id:
            raise ValidationError(
                "Duplicate review pair must contain two distinct claims."
            )
        if self.claim_a_id and self.claim_b_id:
            if self.claim_a_id > self.claim_b_id:
                raise ValidationError(
                    "Duplicate review pairs must use canonical ordering."
                )
            if self.claim_a.thesis_id != self.claim_b.thesis_id:
                raise ValidationError(
                    "Duplicate review pairs must stay within the same thesis."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ClaimEntity(models.Model):
    name = models.CharField(max_length=200)
    canonical_name = models.CharField(max_length=200, unique=True)
    entity_type = models.CharField(max_length=64, default="concept")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["canonical_name", "id"]

    def __str__(self):
        return self.canonical_name


class ClaimPredicate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class ClaimTriple(models.Model):
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="triples",
    )
    subject_entity = models.ForeignKey(
        ClaimEntity,
        on_delete=models.CASCADE,
        related_name="subject_triples",
    )
    predicate = models.ForeignKey(
        ClaimPredicate,
        on_delete=models.CASCADE,
        related_name="triples",
    )
    object_entity = models.ForeignKey(
        ClaimEntity,
        on_delete=models.CASCADE,
        related_name="object_triples",
    )
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-confidence", "-created_at", "-id"]

    def __str__(self):
        return (
            f"{self.subject_entity.canonical_name} "
            f"{self.predicate.name} "
            f"{self.object_entity.canonical_name}"
        )


class ClaimNormalized(models.Model):
    claim = models.OneToOneField(
        Claim,
        on_delete=models.CASCADE,
        related_name="normalized",
    )
    triple = models.ForeignKey(
        ClaimTriple,
        on_delete=models.CASCADE,
        related_name="normalized_claims",
    )
    normalization_method = models.CharField(max_length=120)
    confidence = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-confidence", "claim_id"]

    def __str__(self):
        return f"Normalized claim {self.claim_id}"


class ClaimAlias(models.Model):
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="aliases",
    )
    alias_text = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["alias_text", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["claim", "alias_text"],
                name="uniq_claim_alias_text",
            )
        ]

    def __str__(self):
        return self.alias_text


class ClaimInferenceRule(models.Model):
    name = models.CharField(max_length=120, unique=True)
    pattern_predicate_a = models.CharField(max_length=120)
    pattern_predicate_b = models.CharField(max_length=120)
    inferred_predicate = models.CharField(max_length=120)
    confidence_weight = models.FloatField(default=0.5)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class ClaimInference(models.Model):
    source_claim_a = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="inference_sources_a",
    )
    source_claim_b = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="inference_sources_b",
    )
    inferred_claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="generated_inferences",
    )
    rule = models.ForeignKey(
        ClaimInferenceRule,
        on_delete=models.CASCADE,
        related_name="claim_inferences",
    )
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-confidence", "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_claim_a=models.F("source_claim_b")),
                name="claim_inference_distinct_sources",
            ),
            models.UniqueConstraint(
                fields=["source_claim_a", "source_claim_b", "inferred_claim", "rule"],
                name="uniq_claim_inference_rule_application",
            ),
        ]
        indexes = [
            models.Index(fields=["source_claim_a", "created_at"]),
            models.Index(fields=["source_claim_b", "created_at"]),
            models.Index(fields=["inferred_claim", "created_at"]),
        ]

    def __str__(self):
        return (
            f"Inference {self.source_claim_a_id}+{self.source_claim_b_id}"
            f" => {self.inferred_claim_id}"
        )

    def clean(self):
        super().clean()
        claim_ids = [
            claim.thesis_id
            for claim in (self.source_claim_a, self.source_claim_b, self.inferred_claim)
            if claim is not None and claim.pk is not None
        ]
        if claim_ids and len(set(claim_ids)) > 1:
            raise ValidationError("Claim inference rows must stay within one thesis.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ClaimContradiction(models.Model):
    claim_a = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="contradictions_left",
    )
    claim_b = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="contradictions_right",
    )
    contradiction_type = models.CharField(max_length=120)
    confidence = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-confidence", "claim_a_id", "claim_b_id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(claim_a__lt=models.F("claim_b")),
                name="claim_contradiction_canonical_pair_order",
            ),
            models.UniqueConstraint(
                fields=["claim_a", "claim_b", "contradiction_type"],
                name="uniq_claim_contradiction_pair_type",
            ),
        ]
        indexes = [
            models.Index(fields=["claim_a", "contradiction_type"]),
            models.Index(fields=["claim_b", "contradiction_type"]),
        ]

    def __str__(self):
        return (
            f"Contradiction {self.claim_a_id}<->{self.claim_b_id}"
            f" ({self.contradiction_type})"
        )

    def clean(self):
        super().clean()
        if self.claim_a_id == self.claim_b_id:
            raise ValidationError("Contradiction pair must contain distinct claims.")
        if self.claim_a_id and self.claim_b_id:
            if self.claim_a_id > self.claim_b_id:
                raise ValidationError(
                    "Contradiction pairs must use canonical ordering."
                )
            if self.claim_a.thesis_id != self.claim_b.thesis_id:
                raise ValidationError(
                    "Contradiction pairs must stay within the same thesis."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ClaimSupportClosure(models.Model):
    source_claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="support_closure_sources",
    )
    target_claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="support_closure_targets",
    )
    support_depth = models.PositiveIntegerField(default=1)
    confidence = models.FloatField(default=0.0)

    class Meta:
        ordering = [
            "support_depth",
            "-confidence",
            "source_claim_id",
            "target_claim_id",
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_claim=models.F("target_claim")),
                name="claim_support_closure_distinct_nodes",
            ),
            models.UniqueConstraint(
                fields=["source_claim", "target_claim"],
                name="uniq_claim_support_closure_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["source_claim", "support_depth"]),
            models.Index(fields=["target_claim", "support_depth"]),
        ]

    def __str__(self):
        return f"Support closure {self.source_claim_id}->{self.target_claim_id}"

    def clean(self):
        super().clean()
        if self.source_claim_id and self.target_claim_id:
            if self.source_claim.thesis_id != self.target_claim.thesis_id:
                raise ValidationError(
                    "Support closure rows must stay within the same thesis."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

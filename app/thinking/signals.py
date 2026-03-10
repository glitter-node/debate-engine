from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Claim, ClaimEvidence, ClaimRelation, ClaimVote
from .services.claim_duplicates import refresh_claim_similarity
from .services.claim_graph import calculate_thesis_claim_scores
from .services.claim_inference import rebuild_thesis_inference_safe
from .services.claim_normalization import normalize_claim_safe


@receiver(post_save, sender=ClaimRelation)
@receiver(post_delete, sender=ClaimRelation)
@receiver(post_save, sender=ClaimEvidence)
@receiver(post_delete, sender=ClaimEvidence)
@receiver(post_save, sender=ClaimVote)
@receiver(post_delete, sender=ClaimVote)
def refresh_claim_scores_for_archive_change(sender, instance, **_kwargs):
    claim = getattr(instance, "claim", None)
    if claim is None:
        claim = getattr(instance, "source_claim", None) or getattr(
            instance, "target_claim", None
        )
    if claim is not None:
        calculate_thesis_claim_scores(claim.thesis)
        rebuild_thesis_inference_safe(thesis=claim.thesis)


@receiver(post_save, sender=Claim)
def refresh_claim_similarity_for_claim_save(sender, instance, **_kwargs):
    refresh_claim_similarity(claim=instance)
    normalize_claim_safe(claim=instance)
    rebuild_thesis_inference_safe(thesis=instance.thesis)

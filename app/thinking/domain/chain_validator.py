"""Argument chain validation helpers for service-layer writes."""

from django.core.exceptions import ValidationError

from .relation_rules import MAX_COUNTER_DEPTH


def validate_argument_belongs_to_thesis(
    *, thesis_id: int, argument_thesis_id: int
) -> None:
    if thesis_id != argument_thesis_id:
        raise ValueError("Target argument must belong to the thesis.")


def validate_rebuttal_depth(*, requested_depth: int) -> None:
    if requested_depth < 0:
        raise ValueError("Requested rebuttal depth cannot be negative.")
    if MAX_COUNTER_DEPTH is not None and requested_depth > MAX_COUNTER_DEPTH:
        raise ValueError("Requested rebuttal depth exceeds constrained tree policy.")


def validate_counter_parent_chain(*, counter_id, parent_counter) -> int:
    seen_ids = set()
    if counter_id is not None:
        seen_ids.add(counter_id)

    current = parent_counter
    depth = 0
    while current is not None:
        if current.pk in seen_ids:
            raise ValidationError(
                {"parent_counter": "Counter ancestry cannot contain cycles."}
            )
        seen_ids.add(current.pk)
        depth += 1
        validate_rebuttal_depth(requested_depth=depth)
        current = current.parent_counter
    return depth


def validate_claim_relation_edge(
    *,
    source_claim,
    target_claim,
    relation_id=None,
) -> None:
    if source_claim.thesis_id != target_claim.thesis_id:
        raise ValidationError(
            {"target_claim": "Claim relations must stay within the same thesis."}
        )

    pending_ids = {source_claim.pk}
    stack = [target_claim.pk]
    while stack:
        claim_id = stack.pop()
        if claim_id in pending_ids:
            raise ValidationError(
                {"target_claim": "Claim relation would introduce a graph cycle."}
            )
        pending_ids.add(claim_id)
        outgoing = target_claim.outgoing_relations.model.objects.filter(
            source_claim_id=claim_id
        )
        if relation_id is not None:
            outgoing = outgoing.exclude(pk=relation_id)
        stack.extend(
            outgoing.values_list("target_claim_id", flat=True)
        )


def validate_claim_merge(*, source_claim, target_claim) -> None:
    if source_claim.pk == target_claim.pk:
        raise ValidationError("Source and target claim must be different.")
    if source_claim.thesis_id != target_claim.thesis_id:
        raise ValidationError("Claims can only be merged within the same thesis.")


def validate_claim_merge_graph(
    *,
    thesis_id,
    source_claim_id,
    target_claim_id,
    edge_pairs,
) -> None:
    from thinking.models import ClaimRelation

    adjacency = {}
    relation_qs = (
        ClaimRelation.objects.filter(source_claim__thesis_id=thesis_id)
        .exclude(source_claim_id=source_claim_id)
        .exclude(target_claim_id=source_claim_id)
    )
    for source_id, target_id in relation_qs.values_list(
        "source_claim_id",
        "target_claim_id",
    ):
        adjacency.setdefault(source_id, set()).add(target_id)

    for source_id, target_id in edge_pairs:
        if source_id == target_id:
            raise ValidationError("Claim merge would create a self-referential edge.")
        adjacency.setdefault(source_id, set()).add(target_id)

    visiting = set()
    visited = set()

    def walk(node_id):
        if node_id in visiting:
            raise ValidationError("Claim merge would introduce a graph cycle.")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child_id in adjacency.get(node_id, set()):
            walk(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in list(adjacency):
        walk(node_id)
